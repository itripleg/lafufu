import pytest
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.setting import Setting
from sqlmodel import Session, select


@pytest.fixture
def db(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "test.sqlite"))
    init_db(engine)
    return engine


def test_init_creates_tables(db):
    with Session(db) as s:
        s.exec(select(Setting)).all()


def test_setting_round_trip(db):
    with Session(db) as s:
        s.add(Setting(key="agent.tts.speed", value="0.85", value_type="float"))
        s.commit()
    with Session(db) as s:
        got = s.exec(select(Setting).where(Setting.key == "agent.tts.speed")).one()
        assert got.value == "0.85"
        assert got.value_type == "float"


def test_setting_key_unique(db):
    from sqlalchemy.exc import IntegrityError

    with Session(db) as s:
        s.add(Setting(key="dup", value="a", value_type="str"))
        s.commit()
    with Session(db) as s:
        s.add(Setting(key="dup", value="b", value_type="str"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_check_schema_version_stamps_fresh_db(db, caplog):
    from lafufu_control.db import CURRENT_SCHEMA_VERSION, check_schema_version
    from lafufu_control.models.setting import Setting
    from sqlmodel import Session

    with caplog.at_level("WARNING"):
        check_schema_version(db)
    with Session(db) as s:
        row = s.get(Setting, "bootstrap.schema_version")
    assert row is not None
    assert int(row.value) == CURRENT_SCHEMA_VERSION
    assert "schema" not in caplog.text.lower()  # fresh stamp must NOT warn


def test_check_schema_version_updates_when_db_older(db, caplog):
    """A stale stamp (stored < CURRENT) must be silently updated to CURRENT — not
    a warning that repeats every boot after init_db has already run migrations."""
    from lafufu_control.db import CURRENT_SCHEMA_VERSION, check_schema_version
    from lafufu_control.models.setting import Setting
    from sqlmodel import Session

    with Session(db) as s:
        s.add(
            Setting(
                key="bootstrap.schema_version",
                value=str(CURRENT_SCHEMA_VERSION - 1),
                value_type="int",
            )
        )
        s.commit()
    check_schema_version(db)  # must NOT raise
    # Stamp must now be current.
    with Session(db) as s:
        row = s.get(Setting, "bootstrap.schema_version")
    assert row is not None
    assert int(row.value) == CURRENT_SCHEMA_VERSION


def test_check_schema_version_refuses_when_db_newer(db):
    from lafufu_control.db import CURRENT_SCHEMA_VERSION, check_schema_version
    from lafufu_control.models.setting import Setting
    from sqlmodel import Session

    with Session(db) as s:
        s.add(
            Setting(
                key="bootstrap.schema_version",
                value=str(CURRENT_SCHEMA_VERSION + 1),
                value_type="int",
            )
        )
        s.commit()
    with pytest.raises(RuntimeError):
        check_schema_version(db)


def test_check_schema_version_raises_on_corrupt_value(db):
    """A non-integer stored version must surface as a clear RuntimeError, not a
    raw ValueError traceback — the guard exists to give a diagnosable signal."""
    from lafufu_control.db import check_schema_version
    from lafufu_control.models.setting import Setting
    from sqlmodel import Session

    with Session(db) as s:
        s.add(
            Setting(
                key="bootstrap.schema_version",
                value="not-an-int",
                value_type="int",
            )
        )
        s.commit()
    with pytest.raises(RuntimeError):
        check_schema_version(db)


def test_check_schema_version_updates_stale_stamp(tmp_path):
    """After init_db has run its migrations, a stale schema version stamp must
    be updated to CURRENT_SCHEMA_VERSION so subsequent boots don't spam warnings."""
    from lafufu_control.db import (
        CURRENT_SCHEMA_VERSION,
        _SCHEMA_VERSION_KEY,
        check_schema_version,
        create_engine_for_path,
        init_db,
    )
    from lafufu_control.models.setting import Setting
    from sqlmodel import Session

    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)

    # Manually set a stale version to simulate a DB created before the current version
    with Session(engine) as s:
        row = s.get(Setting, _SCHEMA_VERSION_KEY)
        if row is None:
            row = Setting(key=_SCHEMA_VERSION_KEY, value="0", value_type="int")
            s.add(row)
        else:
            row.value = "0"
        s.commit()

    # check_schema_version should update the stamp after init_db ran migrations
    check_schema_version(engine)

    with Session(engine) as s:
        row = s.get(Setting, _SCHEMA_VERSION_KEY)
        assert row is not None
        assert int(row.value) == CURRENT_SCHEMA_VERSION, (
            f"stamp must be updated to {CURRENT_SCHEMA_VERSION} after migration; got {row.value}"
        )


def test_backup_db_creates_rotating_copies(tmp_path):
    db = tmp_path / "db.sqlite"
    eng = create_engine_for_path(str(db))
    init_db(eng)
    from lafufu_control.db import backup_db

    for _ in range(9):
        backup_db(str(db), keep=7)
    backups = sorted((tmp_path / "backups").glob("db-*.sqlite"))
    assert 1 <= len(backups) <= 7, f"rotation must cap the count at 7; got {len(backups)}"
    assert len(backups) == 7, f"9 distinct backups must prune to exactly keep=7; got {len(backups)}"
    # A backup is a valid, openable SQLite file with the schema.
    import sqlite3

    con = sqlite3.connect(str(backups[-1]))
    try:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        con.close()
