"""Bootstrap default-settings seeding."""

from lafufu_control.bootstrap import seed_default_settings
from lafufu_control.db import init_db
from lafufu_control.models.setting import Setting
from sqlmodel import Session, create_engine, select


def test_seeds_all_expected_keys(tmp_path):
    """Fresh DB should end up with every setting the platform expects."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    seed_default_settings(engine)

    with Session(engine) as s:
        keys = {row.key for row in s.exec(select(Setting)).all()}

    expected_new = {
        # Trigger-mode loop (was env-only)
        "agent.interaction_mode",
        "agent.trigger.phrase",
        "agent.trigger.emotion",
        "agent.trigger.rounds",
        "agent.trigger.print_mode",
        "agent.trigger.print_prompt",
        # Wake-word gate (was env-only)
        "agent.wakeword.enabled",
        "agent.wakeword.model",
        "agent.wakeword.threshold",
        # Mic device picker (was env-only)
        "agent.input_device",
        # Animator servo defaults (subscribers already exist, rows were missing)
        "animator.head_lr.default",
        "animator.head_ud.default",
        "animator.eye.default",
        "animator.jaw.default",
        "animator.brow.default",
    }
    missing = expected_new - keys
    assert not missing, f"bootstrap missing keys: {sorted(missing)}"


def test_servo_defaults_match_canonical_idle_pose(tmp_path):
    """Servo default seeds should be the canonical idle pose constants
    so a freshly-seeded DB produces the same idle pose the animator already uses
    when no override exists."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    seed_default_settings(engine)

    with Session(engine) as s:
        rows = {row.key: row.value for row in s.exec(select(Setting)).all()}

    assert rows["animator.head_lr.default"] == "2063"
    assert rows["animator.head_ud.default"] == "3082"
    assert rows["animator.eye.default"] == "2045"
    assert rows["animator.jaw.default"] == "1728"
    assert rows["animator.brow.default"] == "2075"


def test_reseed_is_idempotent(tmp_path):
    """Existing rows must never be overwritten by re-seeding."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    seed_default_settings(engine)

    # Operator override
    with Session(engine) as s:
        row = s.exec(select(Setting).where(Setting.key == "agent.interaction_mode")).one()
        row.value = "trigger"
        s.add(row)
        s.commit()

    # Re-seed should NOT clobber it
    seed_default_settings(engine)
    with Session(engine) as s:
        row = s.exec(select(Setting).where(Setting.key == "agent.interaction_mode")).one()
        assert row.value == "trigger"
