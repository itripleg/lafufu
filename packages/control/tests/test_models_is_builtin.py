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
