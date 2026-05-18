"""GET /api/state/snapshot - returns everything a browser needs to seed its UI."""

import time

from fastapi import APIRouter, Request
from sqlmodel import Session, select

from ...models.setting import Setting

router = APIRouter()


@router.get("/snapshot")
def snapshot(req: Request):
    with Session(req.app.state.engine) as s:
        settings_rows = s.exec(select(Setting)).all()

    # Include server's notion of "now" so the client can compute heartbeat
    # age relative to the server clock (no skew issues if browser clock differs).
    return {
        "settings": [
            {"key": x.key, "value": x.value, "value_type": x.value_type} for x in settings_rows
        ],
        "services": getattr(req.app.state, "service_status", {}),
        "last_pose": getattr(req.app.state, "last_pose", None),
        "server_now": time.time(),
    }
