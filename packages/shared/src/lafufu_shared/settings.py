"""Env-var loader shared by all services."""

import os
from pathlib import Path


def nats_url() -> str:
    return os.environ.get("LAFUFU_NATS_URL", "nats://localhost:4222")


def data_dir() -> Path:
    """Where persistent state lives (DB, JetStream, backups)."""
    p = Path(os.environ.get("LAFUFU_DATA_DIR", "./var"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "db.sqlite"


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v else default
