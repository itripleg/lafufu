"""System operations: service control via systemd. Uses dict lookup to avoid injection."""

import subprocess

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

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
