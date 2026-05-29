"""System operations: service control via systemd. Uses dict lookup to avoid injection."""

import platform
import re
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _alsa_cards() -> list[str]:
    """ALSA card short-names from `aplay -l` (e.g. 'USB', 'Headphones'). Empty if
    aplay is missing or errors — the UI hides the picker off-Linux anyway."""
    if shutil.which("aplay") is None:
        return []
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, timeout=5, text=True)
    except (subprocess.SubprocessError, OSError):
        return []
    cards: list[str] = []
    for line in out.stdout.splitlines():
        # "card 0: USB [USB Audio Device], device 0: ..."
        m = re.match(r"card \d+: (\S+) \[", line)
        if m and m.group(1) not in cards:
            cards.append(m.group(1))
    return cards


def _alsa_controls() -> list[str]:
    """Simple-mixer control names from `amixer scontrols` (default card)."""
    if shutil.which("amixer") is None:
        return []
    try:
        out = subprocess.run(["amixer", "scontrols"], capture_output=True, timeout=5, text=True)
    except (subprocess.SubprocessError, OSError):
        return []
    names: list[str] = []
    for line in out.stdout.splitlines():
        # "Simple mixer control 'PCM',0"
        m = re.search(r"Simple mixer control '([^']+)'", line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


@router.get("/audio")
def audio_info():
    """Server OS + available ALSA cards/controls. The web UI shows the ALSA
    speaker pickers only when platform == 'linux' (they drive `amixer`, which is
    Linux-only); elsewhere playback falls through to the OS default device."""
    system = platform.system().lower()  # 'linux' | 'windows' | 'darwin'
    on_linux = system == "linux"
    return {
        "platform": system,
        "alsa_cards": _alsa_cards() if on_linux else [],
        "alsa_controls": _alsa_controls() if on_linux else [],
    }


# Validated name → literal systemd unit string. Never interpolate user input.
_SYSTEMCTL_UNITS: dict[str, str] = {
    "agent": "lafufu-agent",
    "animator": "lafufu-animator",
    "printer": "lafufu-printer",
    "control": "lafufu-control",
}


@router.post("/services/{name}/restart")
def restart_service(name: str, req: Request):
    unit = _SYSTEMCTL_UNITS.get(name)
    if unit is None:
        raise HTTPException(
            400, detail={"error_code": "unknown_service", "message": f"unknown service '{name}'"}
        )
    req.app.state.nats_publish(
        "system.service.restarting", {"service": name, "event": "restarting"}
    )
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", unit],
            capture_output=True,
            timeout=15,
        )
    except subprocess.SubprocessError as e:
        raise HTTPException(
            500, detail={"error_code": "systemctl_failed", "message": str(e)}
        ) from e
    if result.returncode != 0:
        raise HTTPException(
            500,
            detail={
                "error_code": "systemctl_nonzero",
                "message": result.stderr.decode(errors="replace") if result.stderr else "",
            },
        )
    return {"ok": True, "service": name}
