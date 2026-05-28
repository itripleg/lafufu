#!/bin/bash
set -euo pipefail

# Lafufu install / update script. Run as root on the Pi.
#
# Usage:
#   sudo ./deploy/install.sh                # fresh install
#   sudo ./deploy/install.sh --update       # update existing install (git pull + reinstall deps)

REPO_DIR="/srv/lafufu"
DATA_DIR="/var/lafufu"
USER_NAME="lafufu"
MODE="${1:-install}"

echo "==> lafufu install ($MODE)"

# 1. System deps
apt-get update

apt-get install -y python3.13 python3.13-venv python3-pip nodejs npm \
                   cups bluez \
                   build-essential libasound2-dev portaudio19-dev \
                   curl ca-certificates git

# chromium: only install if no chromium binary is already present.
# Package name varies (chromium-browser on Bookworm/Pi-OS, chromium on Trixie),
# but if either binary already exists we're done.
if command -v chromium >/dev/null || command -v chromium-browser >/dev/null; then
    echo "==> chromium already present, skipping install"
else
    if apt-get install -y chromium-browser 2>/dev/null; then
        echo "==> installed chromium-browser"
    elif apt-get install -y chromium 2>/dev/null; then
        echo "==> installed chromium"
    else
        echo "WARN: could not install chromium; kiosk service will fail until manual fix" >&2
    fi
fi

# 2. Install uv if missing
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  cp ~/.local/bin/uv /usr/local/bin/uv
fi

# 3. Install nats-server if missing
if ! command -v nats-server >/dev/null; then
  curl -L https://github.com/nats-io/nats-server/releases/download/v2.10.20/nats-server-v2.10.20-linux-arm64.tar.gz | tar xz
  mv nats-server-v2.10.20-linux-arm64/nats-server /usr/local/bin/
  rm -rf nats-server-v2.10.20-linux-arm64
fi

# 4. User + dirs
id -u "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
usermod -aG audio,video,plugdev,dialout,lp "$USER_NAME"
mkdir -p "$DATA_DIR/jetstream"
chown -R "$USER_NAME:$USER_NAME" "$DATA_DIR"

# 5. Repo (when updating, assume the script is already inside /srv/lafufu)
if [[ "$MODE" == "--update" ]]; then
  cd "$REPO_DIR"
  sudo -u "$USER_NAME" git pull --ff-only
else
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "ERROR: $REPO_DIR is not a git checkout. Clone lafufu there first."
    exit 1
  fi
  chown -R "$USER_NAME:$USER_NAME" "$REPO_DIR"
fi

cd "$REPO_DIR"

# 6. Python deps
sudo -u "$USER_NAME" uv sync --all-packages

# 7. Web build → control's static dir
sudo -u "$USER_NAME" bash -c 'cd web && npm ci && npm run build'

# 8. NATS config
mkdir -p /etc/nats
cp deploy/nats/nats-server.production.conf /etc/nats/nats-server.conf
chown root:root /etc/nats/nats-server.conf

# 9a. sudoers fragment — lets the lafufu user restart services + read logs
# without a password (required by the admin "restart service" + "view logs"
# buttons in the control UI). Mode 0440 + visudo syntax check is the standard
# pattern for /etc/sudoers.d/ drops.
install -m 0440 -o root -g root \
    "$REPO_DIR/deploy/sudoers/lafufu-services" \
    /etc/sudoers.d/lafufu-services
visudo -c -f /etc/sudoers.d/lafufu-services

# 9. systemd units
cp deploy/systemd/nats.service /etc/systemd/system/
cp deploy/systemd/lafufu-*.service /etc/systemd/system/
cp deploy/systemd/lafufu.target /etc/systemd/system/
systemctl daemon-reload

# 9c. journald size cap — keep SD-card log growth bounded (drop-in overrides
# the global journald.conf SystemMaxUse).
mkdir -p /etc/systemd/journald.conf.d
cp deploy/systemd/journald-lafufu.conf /etc/systemd/journald.conf.d/journald-lafufu.conf
systemctl restart systemd-journald

# 9b. Bluetooth: stop the discoverable window from auto-expiring (0 = no
#     timeout) so lafufu-btcast controls visibility itself — discoverable
#     while online, hidden while offline.
if [[ -f /etc/bluetooth/main.conf ]]; then
  if grep -qE '^[[:space:]]*#?[[:space:]]*DiscoverableTimeout' /etc/bluetooth/main.conf; then
    sed -i -E 's/^[[:space:]]*#?[[:space:]]*DiscoverableTimeout.*/DiscoverableTimeout = 0/' \
      /etc/bluetooth/main.conf
  else
    sed -i '/^\[General\]/a DiscoverableTimeout = 0' /etc/bluetooth/main.conf
  fi
  systemctl restart bluetooth || true
fi

# 10. Enable + start
systemctl enable nats.service
systemctl enable lafufu-animator.service lafufu-agent.service \
                 lafufu-printer.service lafufu-control.service \
                 lafufu-btcast.service lafufu-kiosk.service lafufu.target

if [[ "$MODE" == "--update" ]]; then
  systemctl restart lafufu.target
else
  systemctl start nats.service
  systemctl start lafufu.target
fi

echo "==> done. Check:  systemctl status 'lafufu-*'"
echo "    Logs:        journalctl -u 'lafufu-*' -f"
echo "    Admin UI:    http://$(hostname -I | awk '{print $1}'):8080/admin"
