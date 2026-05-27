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


def _seed_old_wakeword_rows(engine, *, enabled: str, model: str) -> None:
    """Helper: simulate a Pi bootstrapped before the trained model shipped
    by inserting the pre-PR wakeword rows directly, BEFORE running the new
    seed_default_settings (which would otherwise insert the new defaults)."""
    with Session(engine) as session:
        session.add(Setting(key="agent.wakeword.enabled", value=enabled, value_type="bool"))
        session.add(Setting(key="agent.wakeword.model", value=model, value_type="str"))
        session.commit()


def test_wakeword_lafufu_v1_migration_upgrades_pre_pr_defaults(tmp_path):
    """Existing Pi with pre-PR wakeword defaults gets flipped to the trained
    lafufu model, and the migration flag row is written."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    _seed_old_wakeword_rows(engine, enabled="false", model="hey_jarvis_v0.1")

    seed_default_settings(engine)

    with Session(engine) as session:
        rows = {r.key: r.value for r in session.exec(select(Setting)).all()}
    assert rows["agent.wakeword.enabled"] == "true"
    assert rows["agent.wakeword.model"] == "assets/wakeword/lafufu.onnx"
    assert rows["bootstrap.migrations.wakeword_lafufu_v1"] == "1"


def test_wakeword_lafufu_v1_migration_preserves_operator_overrides(tmp_path):
    """If the operator already changed either wakeword setting to a non-pre-PR
    value, the migration must leave those rows alone but still record its flag."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    # Operator already flipped enabled and picked a custom model.
    _seed_old_wakeword_rows(engine, enabled="true", model="hey_mycroft_v0.1")

    seed_default_settings(engine)

    with Session(engine) as session:
        rows = {r.key: r.value for r in session.exec(select(Setting)).all()}
    assert rows["agent.wakeword.enabled"] == "true"
    assert rows["agent.wakeword.model"] == "hey_mycroft_v0.1"
    assert rows["bootstrap.migrations.wakeword_lafufu_v1"] == "1"


def test_wakeword_lafufu_v1_migration_is_idempotent(tmp_path):
    """Second run must no-op even if rows have been manually reset to pre-PR
    values in between - the flag short-circuits the migration."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    _seed_old_wakeword_rows(engine, enabled="false", model="hey_jarvis_v0.1")

    seed_default_settings(engine)  # first run flips values and writes flag

    # Operator manually reverts to the old values (e.g. to test something).
    with Session(engine) as session:
        en = session.exec(select(Setting).where(Setting.key == "agent.wakeword.enabled")).one()
        en.value = "false"
        session.add(en)
        mo = session.exec(select(Setting).where(Setting.key == "agent.wakeword.model")).one()
        mo.value = "hey_jarvis_v0.1"
        session.add(mo)
        session.commit()

    seed_default_settings(engine)  # second run: flag is set, must no-op

    with Session(engine) as session:
        rows = {r.key: r.value for r in session.exec(select(Setting)).all()}
    assert rows["agent.wakeword.enabled"] == "false"
    assert rows["agent.wakeword.model"] == "hey_jarvis_v0.1"
    assert rows["bootstrap.migrations.wakeword_lafufu_v1"] == "1"
