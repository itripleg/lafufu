"""Run the control API + SPA standalone, with NO NATS broker.

For local visual demos (e.g. the Studio sprite animations). The real service
(`python -m lafufu_control`) connects NATS *before* serving HTTP, so it can't
boot without a broker. This launcher reuses the same app factory, DB engine,
and seeds, but injects a no-op publish — so frames/expressions CRUD, the image
library, and the SPA all work. NATS-only features (live reactive refresh,
servo intents) become harmless no-ops: the Studio playback is frontend-only,
so the sprite animation still renders.

    python scripts/demo_control_nonats.py
    -> http://localhost:8080/admin  (Studio tab)
"""

import time

import uvicorn
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.bootstrap import seed_default_settings
from lafufu_control.db import (
    check_schema_version,
    create_engine_for_path,
    init_db,
)
from lafufu_shared import settings


def main() -> None:
    engine = create_engine_for_path(str(settings.db_path()))
    init_db(engine)
    check_schema_version(engine)
    seed_default_settings(engine)
    seed_animations(engine)

    app = create_app(engine=engine, nats_publish=lambda subject, payload: None)
    app.state.service_status = {}
    app.state.last_pose = None
    app.state.server_now = lambda: time.time()

    print("control (no-NATS demo) -> http://localhost:8080/admin", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")


if __name__ == "__main__":
    main()
