#!/bin/bash
# lafufu-btcast — advertise the Pi's LAN IP as its Bluetooth adapter name.
#
# While the Pi is on a network the adapter is made discoverable and its
# alias is set to "lafufu <ip>", so the IP shows up in any phone's
# Bluetooth device list with no app and no pairing. While offline the
# adapter is made non-discoverable, so its absence means "no network".
#
# Runs as the lafufu-btcast systemd service (as root).
set -u

PYTHON="/srv/lafufu/.venv/bin/python"
POLL_INTERVAL=30
CONTROL_SETTINGS_URL="http://127.0.0.1:8080/api/settings"
last_ip=""

# Whether the operator has left the IP broadcast enabled (btcast.enabled
# setting, toggled from the admin). Fail-open: if control is unreachable, the
# setting is absent, or auth blocks the loopback read, keep broadcasting so a
# control hiccup can't silently hide the Pi's IP.
broadcast_enabled() {
    local val
    val="$(curl -fsS --max-time 3 "$CONTROL_SETTINGS_URL" 2>/dev/null \
        | "$PYTHON" -c 'import sys, json; rows = json.load(sys.stdin); print(next((r["value"] for r in rows if r.get("key") == "btcast.enabled"), "true"))' 2>/dev/null)" || val="true"
    [[ "$val" != "false" ]]
}

bluetoothctl power on >/dev/null 2>&1 || true

while true; do
    if ! broadcast_enabled; then
        bluetoothctl discoverable off >/dev/null 2>&1 || true
        if [[ "$last_ip" != "__disabled__" ]]; then
            echo "lafufu-btcast: disabled via btcast.enabled setting, adapter hidden"
            last_ip="__disabled__"
        fi
        sleep "$POLL_INTERVAL"
        continue
    fi

    ip="$("$PYTHON" -m lafufu_shared.netinfo 2>/dev/null || true)"

    if [[ -n "$ip" ]]; then
        bluetoothctl discoverable on >/dev/null 2>&1 || true
        if [[ "$ip" != "$last_ip" ]]; then
            bluetoothctl system-alias "lafufu $ip" >/dev/null 2>&1 || true
            echo "lafufu-btcast: online, alias set to 'lafufu $ip'"
            last_ip="$ip"
        fi
    else
        bluetoothctl discoverable off >/dev/null 2>&1 || true
        if [[ -n "$last_ip" ]]; then
            echo "lafufu-btcast: offline, adapter hidden"
            last_ip=""
        fi
    fi

    sleep "$POLL_INTERVAL"
done
