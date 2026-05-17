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
