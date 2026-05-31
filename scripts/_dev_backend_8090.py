"""Throwaway dev launcher: serve THIS worktree's control API on :8090 with a
local SQLite DB (migrated + seeded), no NATS. Lets the studio single-media work
be eyeballed in the browser via the Vite proxy. Not committed-for-prod."""

import os

import uvicorn
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.bootstrap import seed_default_settings
from lafufu_control.db import create_engine_for_path, init_db

DB = os.environ.get("LAFUFU_DEV_DB", "C:/tmp/lafufu-studio-dev.sqlite")

engine = create_engine_for_path(DB)
init_db(engine)
seed_default_settings(engine)
seed_animations(engine)

app = create_app(engine=engine, nats_publish=lambda subject, payload: None)
print("WORKTREE_BACKEND_UP_8090 db=" + DB, flush=True)
uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")
