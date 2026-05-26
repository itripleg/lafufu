"""The control DB engine must set busy_timeout + WAL on every connection
so concurrent writes from the FastAPI handler thread and the asyncio chat
persistence path don't 500 with `database is locked`."""

import concurrent.futures

from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.setting import Setting
from sqlmodel import Session


def test_busy_timeout_pragma_set(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with engine.connect() as conn:
        # busy_timeout is per-connection; we just confirm it's non-zero on a
        # freshly handed-out connection.
        row = conn.exec_driver_sql("PRAGMA busy_timeout").fetchone()
        assert row[0] >= 5000


def test_journal_mode_wal(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with engine.connect() as conn:
        row = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()
        assert row[0].lower() == "wal"


def test_concurrent_writes_succeed(tmp_path):
    """Two writes to the same key from separate threads must both succeed
    within the busy_timeout. Without busy_timeout (default 0), the loser
    raises immediately with 'database is locked'."""
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)

    def write_setting(key: str, value: str) -> None:
        with Session(engine) as s:
            row = Setting(key=key, value=value, value_type="str")
            s.add(row)
            s.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(write_setting, "k1", "v1")
        f2 = ex.submit(write_setting, "k2", "v2")
        f1.result()  # raises on failure
        f2.result()

    with Session(engine) as s:
        assert s.get(Setting, "k1") is not None
        assert s.get(Setting, "k2") is not None
