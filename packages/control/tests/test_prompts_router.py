"""Tests for the prompt switcher: /api/agent/prompts/*.

Selecting / editing / restoring the ACTIVE preset must drive
`agent.system_prompt` (the live value the running agent reloads) through the
SAME persist+publish path the normal settings PATCH uses, so the agent gets a
`config.changed.agent.system_prompt` event.
"""

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.bootstrap import seed_default_settings
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.setting import Setting
from lafufu_shared.prompts import DEFAULT_SYSTEM_PROMPT, FORTUNE_TELLER_PROMPT
from sqlmodel import Session


@pytest.fixture
def env(tmp_path):
    """Seeded DB + TestClient + captured publisher (subject, payload) list."""
    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "prompts.sqlite"))
    init_db(engine)
    seed_default_settings(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    return TestClient(app), engine, published


def _value(engine, key: str) -> str:
    with Session(engine) as s:
        row = s.get(Setting, key)
        return row.value if row else None


def _subjects(published) -> list[str]:
    return [s for s, _ in published]


def test_get_lists_both_presets_with_active_and_is_default(env):
    c, _engine, _pub = env
    r = c.get("/api/agent/prompts")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == "street_oracle"
    presets = body["presets"]
    assert [p["id"] for p in presets] == ["street_oracle", "fortune_teller"]
    so, ft = presets
    assert so["label"] == "Street Oracle"
    assert so["text"] == DEFAULT_SYSTEM_PROMPT
    assert so["is_default"] is True
    assert ft["label"] == "Fortune Teller"
    assert ft["text"] == FORTUNE_TELLER_PROMPT
    assert ft["is_default"] is True


def test_select_switches_preset_and_drives_system_prompt(env):
    c, engine, published = env
    published.clear()
    r = c.post("/api/agent/prompts/select", json={"id": "fortune_teller"})
    assert r.status_code == 200
    assert r.json()["active"] == "fortune_teller"

    assert _value(engine, "agent.prompt_preset") == "fortune_teller"
    assert _value(engine, "agent.system_prompt") == FORTUNE_TELLER_PROMPT

    subjects = _subjects(published)
    assert "config.changed.agent.prompt_preset" in subjects
    assert "config.changed.agent.system_prompt" in subjects


def test_select_rejects_invalid_id(env):
    c, _engine, _pub = env
    r = c.post("/api/agent/prompts/select", json={"id": "tarot_reader"})
    assert r.status_code in (400, 422)


def test_edit_active_preset_updates_both_and_publishes(env):
    c, engine, published = env
    # street_oracle is active by default
    published.clear()
    new_text = "[neutral] A brand new oracle voice."
    r = c.put("/api/agent/prompts/street_oracle", json={"text": new_text})
    assert r.status_code == 200
    body = r.json()
    so = next(p for p in body["presets"] if p["id"] == "street_oracle")
    assert so["text"] == new_text
    assert so["is_default"] is False

    assert _value(engine, "agent.prompt.street_oracle") == new_text
    assert _value(engine, "agent.system_prompt") == new_text
    assert "config.changed.agent.system_prompt" in _subjects(published)


def test_edit_non_active_preset_leaves_system_prompt_untouched(env):
    c, engine, published = env
    # street_oracle is active; edit fortune_teller (non-active)
    published.clear()
    new_text = "[happy] An edited fortune voice."
    r = c.put("/api/agent/prompts/fortune_teller", json={"text": new_text})
    assert r.status_code == 200

    assert _value(engine, "agent.prompt.fortune_teller") == new_text
    # live prompt must NOT have changed
    assert _value(engine, "agent.system_prompt") == DEFAULT_SYSTEM_PROMPT
    assert "config.changed.agent.system_prompt" not in _subjects(published)
    # but the preset's own setting did publish
    assert "config.changed.agent.prompt.fortune_teller" in _subjects(published)


def test_edit_rejects_invalid_id(env):
    c, _engine, _pub = env
    r = c.put("/api/agent/prompts/tarot_reader", json={"text": "x"})
    assert r.status_code in (400, 422)


def test_edit_rejects_overlong_text(env):
    c, _engine, _pub = env
    r = c.put("/api/agent/prompts/street_oracle", json={"text": "a" * 4001})
    assert r.status_code == 422


def test_restore_active_preset_resets_text_and_system_prompt(env):
    c, engine, published = env
    # First edit the active preset away from default
    c.put("/api/agent/prompts/street_oracle", json={"text": "[neutral] junk"})
    assert _value(engine, "agent.prompt.street_oracle") == "[neutral] junk"

    published.clear()
    r = c.post("/api/agent/prompts/street_oracle/restore")
    assert r.status_code == 200
    body = r.json()
    so = next(p for p in body["presets"] if p["id"] == "street_oracle")
    assert so["text"] == DEFAULT_SYSTEM_PROMPT
    assert so["is_default"] is True

    assert _value(engine, "agent.prompt.street_oracle") == DEFAULT_SYSTEM_PROMPT
    assert _value(engine, "agent.system_prompt") == DEFAULT_SYSTEM_PROMPT
    assert "config.changed.agent.system_prompt" in _subjects(published)


def test_restore_non_active_preset_leaves_system_prompt_untouched(env):
    c, engine, published = env
    # Edit the non-active fortune_teller, then restore it
    c.put("/api/agent/prompts/fortune_teller", json={"text": "[happy] junk"})
    published.clear()
    r = c.post("/api/agent/prompts/fortune_teller/restore")
    assert r.status_code == 200

    assert _value(engine, "agent.prompt.fortune_teller") == FORTUNE_TELLER_PROMPT
    # active is still street_oracle → its live prompt is untouched
    assert _value(engine, "agent.system_prompt") == DEFAULT_SYSTEM_PROMPT
    assert "config.changed.agent.system_prompt" not in _subjects(published)


def test_restore_rejects_invalid_id(env):
    c, _engine, _pub = env
    r = c.post("/api/agent/prompts/tarot_reader/restore")
    assert r.status_code in (400, 422)
