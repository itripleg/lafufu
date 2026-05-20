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
last_ip=""

bluetoothctl power on >/dev/null 2>&1 || true

while true; do
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
