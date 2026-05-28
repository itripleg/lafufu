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
