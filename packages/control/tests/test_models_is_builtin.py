import sqlite3

from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models import Expression, Frame
from sqlmodel import Session


def test_frame_is_builtin_defaults_false(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Frame(name="x", head_lr=0, head_ud=0, eye=0, jaw=0, brow=0))
        s.commit()
        f = s.get(Frame, "x")
        assert f.is_builtin is False


def test_expression_is_builtin_defaults_false(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Expression(name="x"))
        s.commit()
        e = s.get(Expression, "x")
        assert e.is_builtin is False


def test_frame_is_builtin_can_be_true(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Frame(name="x", head_lr=0, head_ud=0, eye=0, jaw=0, brow=0, is_builtin=True))
        s.commit()
        assert s.get(Frame, "x").is_builtin is True


def test_migration_adds_is_builtin_to_existing_db(tmp_path):
    """init_db on a DB that pre-dates is_builtin should ALTER TABLE to add the column."""
    db_path = str(tmp_path / "legacy.sqlite")

    # Bootstrap a DB with the old schema (no is_builtin column).
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE frame (name TEXT PRIMARY KEY, head_lr INT, head_ud INT,"
        " eye INT, jaw INT, brow INT, image TEXT, description TEXT)"
    )
    con.execute(
        "CREATE TABLE expression (name TEXT PRIMARY KEY, playback TEXT NOT NULL DEFAULT 'once',"
        " default_duration_ms INT NOT NULL DEFAULT 250, default_delay_ms INT NOT NULL DEFAULT 80,"
        " default_easing TEXT NOT NULL DEFAULT 'ease-in-out', steps_json TEXT NOT NULL DEFAULT '[]',"
        " emotion TEXT UNIQUE, description TEXT)"
    )
    con.commit()
    con.close()

    engine = create_engine_for_path(db_path)
    init_db(engine)  # must not raise, and must add the column

    with Session(engine) as s:
        s.add(Frame(name="x", head_lr=0, head_ud=0, eye=0, jaw=0, brow=0))
        s.commit()
        assert s.get(Frame, "x").is_builtin is False


def test_expression_is_builtin_can_be_true(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Expression(name="x", is_builtin=True))
        s.commit()
        assert s.get(Expression, "x").is_builtin is True
