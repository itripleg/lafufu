"""SQLite engine + session helpers."""

import logging
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger(__name__)


def create_engine_for_path(path: str):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout = 5000")  # wait up to 5 s instead of immediately erroring
        cur.execute("PRAGMA journal_mode = WAL")  # reduces write-write contention; idempotent
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.close()

    return engine


def init_db(engine) -> None:
    from .models import behavior, chat, expression, frame, plugin, setting  # noqa: F401

    SQLModel.metadata.create_all(engine)
    # Additive migrations for existing on-disk DBs.
    with engine.connect() as conn:
        for table in ("frame", "expression"):
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if "is_builtin" not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0"
                )
        conn.commit()


def get_session(engine) -> Generator[Session]:
    with Session(engine) as session:
        yield session


def backup_db(db_path: str, keep: int = 7) -> None:
    """Copy *db_path* to ``<parent>/backups/db-<timestamp>.sqlite`` using the
    SQLite online-backup API so the snapshot is consistent even under WAL mode.

    Retains only the *keep* most-recent copies (sorted by filename, which is
    chronological because the timestamp is ISO-like with microsecond precision).
    A counter suffix is appended when a filename collision occurs so that rapid
    successive calls always produce distinct files.

    Any failure is logged as a warning and silently swallowed — a missing backup
    must never prevent the service from starting.
    """
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    try:
        backup_dir = Path(db_path).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        stem = datetime.now().strftime("db-%Y%m%d-%H%M%S-%f")
        dest = backup_dir / f"{stem}.sqlite"
        # Collision fallback: append a counter so rapid calls always yield distinct files.
        counter = 0
        while dest.exists():
            counter += 1
            dest = backup_dir / f"{stem}-{counter}.sqlite"

        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        # Prune oldest backups, keeping only *keep* most recent.
        all_backups = sorted(backup_dir.glob("db-*.sqlite"))
        for old in all_backups[:-keep]:
            old.unlink()

        log.debug("db.backup.created path=%s", dest)
    except Exception as e:
        log.warning("db.backup.failed error=%s", e)
