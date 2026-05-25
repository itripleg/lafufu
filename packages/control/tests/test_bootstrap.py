"""Bootstrap default-settings seeding."""

from lafufu_animator.pose import (
    BROW_IDLE_DXL,
    EYE_IDLE_DXL,
    HEAD_IDLE_LR_DXL,
    HEAD_IDLE_UD_DXL,
    MOUTH_CLOSE_DXL,
)
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
    """Servo defaults equal the canonical idle pose constants from `pose.py`
    so a fresh DB starts the robot in the correct position."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    seed_default_settings(engine)

    with Session(engine) as s:
        rows = {row.key: row.value for row in s.exec(select(Setting)).all()}

    assert rows["animator.head_lr.default"] == str(HEAD_IDLE_LR_DXL)
    assert rows["animator.head_ud.default"] == str(HEAD_IDLE_UD_DXL)
    assert rows["animator.eye.default"] == str(EYE_IDLE_DXL)
    assert rows["animator.jaw.default"] == str(MOUTH_CLOSE_DXL)
    assert rows["animator.brow.default"] == str(BROW_IDLE_DXL)


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
