from lafufu_control.animation.seed import SEED_EXPRESSIONS, SEED_FRAMES, seed_animations
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models import Expression, Frame
from sqlmodel import Session, select


def _engine(tmp_path):
    e = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(e)
    return e


def test_seed_on_empty_db_inserts_all_with_is_builtin(tmp_path):
    engine = _engine(tmp_path)
    seed_animations(engine)
    with Session(engine) as s:
        frames = s.exec(select(Frame)).all()
        exprs = s.exec(select(Expression)).all()
        assert len(frames) == len(SEED_FRAMES)
        assert len(exprs) == len(SEED_EXPRESSIONS)
        assert all(f.is_builtin for f in frames)
        assert all(e.is_builtin for e in exprs)


def test_seed_does_not_clobber_user_edits(tmp_path):
    engine = _engine(tmp_path)
    seed_animations(engine)
    with Session(engine) as s:
        e = s.get(Expression, "happy")
        e.description = "user-edited"
        s.add(e)
        s.commit()
    seed_animations(engine)  # second run
    with Session(engine) as s:
        assert s.get(Expression, "happy").description == "user-edited"


def test_seed_runs_after_user_creates_a_row(tmp_path):
    """Regression: old bail-if-anything-exists seed skipped built-ins entirely
    when a user had created a single custom expression. Per-row should still
    insert built-ins."""
    engine = _engine(tmp_path)
    with Session(engine) as s:
        s.add(Expression(name="my_custom"))
        s.commit()
    seed_animations(engine)
    with Session(engine) as s:
        for name, *_ in SEED_EXPRESSIONS:
            assert s.get(Expression, name) is not None, f"missing seed: {name}"
        assert s.get(Expression, "my_custom").is_builtin is False


def test_seed_backfills_is_builtin_on_pre_existing_rows(tmp_path):
    """If the seed ran before is_builtin existed, existing rows need the flag
    set when the new seed runs."""
    engine = _engine(tmp_path)
    with Session(engine) as s:
        first_seed_name = SEED_EXPRESSIONS[0][0]
        s.add(Expression(name=first_seed_name, is_builtin=False))
        s.commit()
    seed_animations(engine)
    with Session(engine) as s:
        assert s.get(Expression, first_seed_name).is_builtin is True
