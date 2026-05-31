"""System operations: service control via systemd. Uses dict lookup to avoid injection."""

import platform
import re
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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


def _parse_nmcli_conns(text: str) -> list[str]:
    """Wi-Fi profile NAMEs from `nmcli -t -f NAME,TYPE connection show` output.

    ``-t`` (terse) is one ``NAME:TYPE`` per line; nmcli escapes any ``:`` inside
    NAME as ``\\:``. TYPE never contains ``:``, so the real field separator is the
    last unescaped colon — rpartition on ``:`` splits it, then we unescape NAME.
    """
    names: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        name, _, typ = line.rpartition(":")
        if "wireless" in typ and name:
            names.append(name.replace("\\:", ":"))
    return names


def _nmcli(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["nmcli", "-t", *args], capture_output=True, timeout=8, text=True
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def _wifi_state() -> tuple[str | None, list[str]]:
    """(active_wifi_profile_or_None, saved_wifi_profile_names)."""
    saved = _parse_nmcli_conns(_nmcli(["-f", "NAME,TYPE", "connection", "show"]))
    active_list = _parse_nmcli_conns(_nmcli(["-f", "NAME,TYPE", "connection", "show", "--active"]))
    return (active_list[0] if active_list else None), saved


@router.get("/wifi")
def wifi_info():
    """Saved Wi-Fi networks the Pi can switch to + the one it's on now.

    Barebones: only networks already saved on the Pi are offered (switching to a
    saved profile needs no password). ``available`` is false off-Linux or when
    NetworkManager/nmcli isn't present, so the UI hides the picker.
    """
    if platform.system().lower() != "linux" or shutil.which("nmcli") is None:
        return {"available": False, "current": None, "networks": []}
    current, networks = _wifi_state()
    return {"available": True, "current": current, "networks": networks}


class WifiConnect(BaseModel):
    name: str


@router.post("/wifi/connect")
def wifi_connect(body: WifiConnect, req: Request):
    """Switch the Pi to a SAVED Wi-Fi network. The name is validated against the
    saved-profile list (no arbitrary input reaches the shell). Uses ``nmcli -w 0``
    so it returns immediately — NetworkManager flips the link in the background,
    which means THIS request's own connection may drop if you're switching the
    network you're viewing from; reconnect on the new network."""
    if shutil.which("nmcli") is None:
        raise HTTPException(
            400, detail={"error_code": "no_nmcli", "message": "NetworkManager not available"}
        )
    _, saved = _wifi_state()
    if body.name not in saved:
        raise HTTPException(
            400,
            detail={
                "error_code": "unknown_network",
                "message": f"'{body.name}' is not a saved Wi-Fi network",
            },
        )
    try:
        result = subprocess.run(
            ["sudo", "-n", "nmcli", "-w", "0", "connection", "up", "id", body.name],
            capture_output=True,
            timeout=15,
        )
    except subprocess.SubprocessError as e:
        raise HTTPException(500, detail={"error_code": "nmcli_failed", "message": str(e)}) from e
    if result.returncode != 0:
        raise HTTPException(
            500,
            detail={
                "error_code": "nmcli_nonzero",
                "message": result.stderr.decode(errors="replace") if result.stderr else "",
            },
        )
    return {"ok": True, "switching_to": body.name}


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
