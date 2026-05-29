#!/bin/bash
set -euo pipefail

# deploy.sh — idempotent Pi-side update for /srv/lafufu.
#
# Run ON THE PI as the lafufu user (or root) from any directory.
# Pulls the latest code, syncs deps, rebuilds the web SPA if needed,
# and restarts the four application services one-by-one (matching the
# NOPASSWD entries in /etc/sudoers.d/lafufu-services).
#
# Usage:
#   cd /srv/lafufu && ./deploy/deploy.sh

# --- sanity: must be run against the real checkout ---
if [[ ! -d /srv/lafufu/.git ]]; then
    echo "ERROR: /srv/lafufu/.git not found. This script must be run from the" >&2
    echo "       /srv/lafufu checkout on the Pi. Clone the repo there first." >&2
    exit 1
fi

cd /srv/lafufu

echo "==> lafufu deploy — $(date '+%Y-%m-%d %H:%M:%S')"

# --- fetch latest refs ---
echo "==> git fetch"
git fetch

# --- dirty-tree guard: never auto-discard local changes ---
if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: working tree has uncommitted changes. Commit or stash them" >&2
    echo "       before deploying, e.g.:" >&2
    echo "         git stash" >&2
    echo "       then re-run this script. After deploy you can restore with:" >&2
    echo "         git stash pop" >&2
    exit 1
fi

# --- pull ---
before=$(git rev-parse HEAD)
echo "==> git pull --ff-only (before: ${before:0:12})"
git pull --ff-only
after=$(git rev-parse HEAD)

if [[ "$before" == "$after" ]]; then
    echo "==> already up to date (${after:0:12}) — proceeding to sync + restart for idempotency"
else
    echo "==> updated ${before:0:12}..${after:0:12}"
fi

# --- Python deps ---
echo "==> uv sync --all-packages"
uv sync --all-packages

# --- web rebuild (only if web/ changed between the two SHAs) ---
if git diff --name-only "$before" "$after" | grep -q '^web/'; then
    echo "==> web/ changed — rebuilding SPA"
    (cd web && npm ci && npm run build)
else
    echo "==> web/ unchanged — skipping SPA build"
fi

# --- restart services one-at-a-time (must match NOPASSWD entries exactly) ---
echo "==> restarting services"
for svc in lafufu-control lafufu-agent lafufu-animator lafufu-printer; do
    echo "==> restarting $svc"
    sudo /usr/bin/systemctl restart "$svc.service"
done

# --- health check ---
echo "==> health check"
all_ok=true
for svc in lafufu-control lafufu-agent lafufu-animator lafufu-printer; do
    state=$(systemctl is-active "$svc.service" 2>/dev/null || true)
    if [[ "$state" == "active" ]]; then
        echo "    $svc: $state"
    else
        echo "    WARN: $svc is $state (expected active)"
        all_ok=false
    fi
done

if $all_ok; then
    echo "==> all services active"
else
    echo "==> WARN: one or more services did not come up — check: journalctl -u 'lafufu-*' -n 50"
fi

echo "==> deploy complete"
