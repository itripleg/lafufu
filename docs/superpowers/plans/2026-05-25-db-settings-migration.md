# DB-Backed Settings Migration + Admin Tab Reorg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote 10 env-var-only knobs (trigger mode, wake-word, mic device) to DB-backed settings live-tunable from the admin UI; seed 5 animator servo defaults that were wired but unsurfaced; reorganize the settings form into service-aligned tabs with a sticky header; fix the `_rebuild_tts` resource leak from PR #14 review.

**Architecture:** Mirror the existing settings pattern — bootstrap row -> control publishes `config.changed.<key>` on write -> service subscribes via `nats_helper.subscribe_model` -> handler mutates local state. Env vars become first-boot seed-only; DB rows take over after `seed_default_settings`. New `/api/agent/input-devices` endpoint mirrors `/api/agent/voices`. Front-end settings form gains new `DYNAMIC_OPTIONS` + `SLIDER_HINTS` entries; `categoryOf` reduces to a prefix-based rule.

**Tech Stack:** Python 3.13 / `uv` workspace; SQLModel + SQLite; FastAPI; NATS (Pydantic schemas via `lafufu_shared.schemas`); SolidJS + TypeScript + Vite (admin web).

**Spec:** `docs/superpowers/specs/2026-05-25-db-settings-migration-design.md`

---

## Task 1: Seed bootstrap rows (15 new settings)

**Files:**
- Modify: `packages/control/src/lafufu_control/bootstrap.py`
- Test: `packages/control/tests/test_bootstrap.py` (create if absent)

Pure-additive: adds new rows to the `DEFAULTS` list. No subscribers yet — the bootstrap test just confirms the new keys appear in the seeded set on a fresh DB. The agent / animator subscribers come in later tasks.

- [ ] **Step 1: Write the failing test**

Create `packages/control/tests/test_bootstrap.py` (or append a new test to an existing file if one exists — check first with `ls packages/control/tests/`):

```python
"""Bootstrap default-settings seeding."""

from sqlmodel import Session, create_engine, select

from lafufu_control.bootstrap import seed_default_settings
from lafufu_control.db import init_db
from lafufu_control.models.setting import Setting


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/control/tests/test_bootstrap.py -v`
Expected: FAIL on `test_seeds_all_expected_keys` with the 15 missing keys.

- [ ] **Step 3: Add the 15 new rows to `bootstrap.py`**

In `packages/control/src/lafufu_control/bootstrap.py`, append these rows to the `DEFAULTS` list **before** the closing `]`. Add them in three contiguous groups so the file stays organized.

Locate the existing `# Animator` comment-block and the row that follows it (the existing `animator.idle_animation.enabled` row). Insert the 5 servo defaults immediately after `animator.idle_animation.enabled` so all animator rows stay together:

```python
    (
        "animator.head_lr.default",
        "2063",
        "int",
        "Default head left/right servo position (DXL units, 1828=right..2298=left). Moves the robot live when changed.",
    ),
    (
        "animator.head_ud.default",
        "3082",
        "int",
        "Default head up/down servo position (DXL units, 2885=up..3278=down). Moves the robot live when changed.",
    ),
    (
        "animator.eye.default",
        "2045",
        "int",
        "Default eye servo position (DXL units, 1960=left..2130=right). Moves the robot live when changed.",
    ),
    (
        "animator.jaw.default",
        "1728",
        "int",
        "Default jaw closed position (DXL units, 1534=open..1728=closed). Moves the robot live when changed.",
    ),
    (
        "animator.brow.default",
        "2075",
        "int",
        "Default brow position (DXL units, 2051=down..2099=up). Moves the robot live when changed.",
    ),
```

Locate the last `agent.*` row (currently `agent.voice_model`) and add the 10 agent rows immediately after it, before the `# Animator` group header:

```python
    (
        "agent.interaction_mode",
        "continuous",
        "str",
        "Interaction loop mode. 'continuous' = listen anything, optionally auto-print. 'trigger' = wake-word-gated guided fortune (requires agent.wakeword.enabled=true).",
    ),
    (
        "agent.trigger.phrase",
        "Welcome, traveler. Ask, and the cards shall reveal.",
        "str",
        "Trigger-mode opening line Lafufu speaks after the wake word fires.",
    ),
    (
        "agent.trigger.emotion",
        "neutral",
        "str",
        "Emotion (face animation) for the trigger-mode opening line. One of: happy, sad, angry, surprised, neutral, agree, disagree.",
    ),
    (
        "agent.trigger.rounds",
        "1",
        "int",
        "Trigger-mode: number of back-and-forth rounds AFTER the opening. 1 = single Q&A; 2+ = conversation.",
    ),
    (
        "agent.trigger.print_mode",
        "ask",
        "str",
        "Trigger-mode print behavior at session end. 'none' = never print; 'auto' = always print the last reply; 'ask' = Lafufu asks the visitor.",
    ),
    (
        "agent.trigger.print_prompt",
        "Would you like a printed fortune?",
        "str",
        "Trigger-mode: line Lafufu speaks before the y/n print listen. Only used when agent.trigger.print_mode='ask'.",
    ),
    (
        "agent.wakeword.enabled",
        "false",
        "bool",
        "Whether the wake-word gate is active. When true, the mic ignores everything until the configured keyword fires (Whisper stays idle). Required for trigger mode.",
    ),
    (
        "agent.wakeword.model",
        "hey_jarvis_v0.1",
        "str",
        "openwakeword model name (one of the bundled defaults, or a path to a custom .onnx).",
    ),
    (
        "agent.wakeword.threshold",
        "0.5",
        "float",
        "Wake-word confidence threshold (0.0-1.0). Lower = more sensitive (more false positives); higher = needs clearer pronunciation.",
    ),
    (
        "agent.input_device",
        "auto",
        "str",
        "Mic device. 'auto' uses the PREFER list -> PyAudio default -> first non-AVOID chain. Otherwise: a numeric PyAudio device index or a name substring (case-insensitive).",
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/control/tests/test_bootstrap.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```
git add packages/control/src/lafufu_control/bootstrap.py packages/control/tests/test_bootstrap.py
git commit -m "feat(control): seed 15 new settings (trigger / wakeword / mic / servo defaults)"
```

---

## Task 2: `GET /api/agent/input-devices` endpoint

**Files:**
- Modify: `packages/control/src/lafufu_control/api/routers/agent.py`
- Test: `packages/control/tests/test_api_agent.py` (append; create if absent)

Mirrors the `/voices` endpoint pattern. Enumerates PyAudio's input devices; first entry is always the `"auto"` sentinel.

- [ ] **Step 1: Write the failing test**

Check if `test_api_agent.py` exists. If yes, append the new tests. If not, create it with the standard FastAPI TestClient pattern:

```python
"""Tests for /api/agent/* endpoints."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from lafufu_control.api.app import create_app


def _make_client():
    app = create_app(engine=MagicMock(), nats_publish=lambda *a, **kw: None, api_token="")
    return TestClient(app)


def test_input_devices_returns_auto_first():
    """`auto` sentinel is the first entry so operators always see it as default."""
    fake_p = MagicMock()
    fake_p.get_device_count.return_value = 0
    with patch(
        "lafufu_control.api.routers.agent.get_pyaudio", return_value=fake_p
    ):
        r = _make_client().get("/api/agent/input-devices")
    assert r.status_code == 200
    devices = r.json()["devices"]
    assert devices[0]["name"] == "auto"
    assert "system default" in devices[0]["label"].lower()


def test_input_devices_enumerates_pyaudio():
    """Real-shape devices show up with numeric index strings as `name`."""
    fake_p = MagicMock()
    fake_p.get_device_count.return_value = 3
    fake_p.get_device_info_by_index.side_effect = [
        {"index": 0, "maxInputChannels": 2, "name": "Microphone Array"},
        {"index": 1, "maxInputChannels": 0, "name": "Speakers"},  # output, skipped
        {"index": 2, "maxInputChannels": 1, "name": "USB Mic"},
    ]
    with patch(
        "lafufu_control.api.routers.agent.get_pyaudio", return_value=fake_p
    ):
        r = _make_client().get("/api/agent/input-devices")
    assert r.status_code == 200
    devices = r.json()["devices"]
    # auto + 2 inputs (index 1 is output-only, skipped)
    assert [d["name"] for d in devices] == ["auto", "0", "2"]
    assert devices[1]["label"] == "Microphone Array"
    assert devices[2]["label"] == "USB Mic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/control/tests/test_api_agent.py -v`
Expected: FAIL — `/api/agent/input-devices` returns 404.

- [ ] **Step 3: Add the endpoint to `agent.py`**

In `packages/control/src/lafufu_control/api/routers/agent.py`, add a top-level helper and a new endpoint. Place the endpoint after the existing `list_voices` function for visual grouping with the other discovery endpoints:

```python
# Lazy import so the control service can run on machines without PyAudio
# installed (PyAudio is an agent dep, not a control dep). Resolved at
# request time inside the endpoint.
def get_pyaudio():
    from lafufu_agent.audio_capture import get_pyaudio as _impl
    return _impl()


@router.get("/input-devices")
async def list_input_devices(_: Request):
    """List PyAudio input devices the agent can bind its mic to.

    First entry is always the ``auto`` sentinel — selecting it falls
    through to the existing PREFER -> PyAudio default -> first-non-avoided
    chain. Other entries' ``name`` field is the numeric PyAudio index as a
    string (matching how ``LAFUFU_INPUT_DEVICE`` and ``agent.input_device``
    parse the value).
    """
    devices: list[dict] = [
        {"name": "auto", "label": "auto — system default", "channels": 0},
    ]
    try:
        p = get_pyaudio()
    except Exception as e:
        # PyAudio not importable on this host (control sometimes runs on
        # machines without ALSA / PortAudio). Return just the sentinel so
        # the dropdown still renders.
        return {"devices": devices, "error": str(e)}

    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            devices.append(
                {
                    "name": str(int(info["index"])),
                    "label": info.get("name", f"device {i}"),
                    "channels": int(info.get("maxInputChannels", 0)),
                }
            )
    return {"devices": devices}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/control/tests/test_api_agent.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```
git add packages/control/src/lafufu_control/api/routers/agent.py packages/control/tests/test_api_agent.py
git commit -m "feat(control): GET /api/agent/input-devices for mic-picker dropdown"
```

---

## Task 3: Frontend `api.listInputDevices()`

**Files:**
- Modify: `web/src/shared/api.ts`
- Test: covered by Task 14's integration test (manual smoke for the dropdown)

Tiny pass-through type-safe wrapper, identical pattern to `listVoices` and `listWhisperModels`.

- [ ] **Step 1: Add the method**

In `web/src/shared/api.ts`, locate the existing `listWhisperModels:` entry and add this immediately after it:

```typescript
  listInputDevices: () =>
    req<{
      devices: Array<{
        name: string;   // "auto" sentinel or numeric PyAudio index as string
        label: string;  // human-readable device name (or "auto — system default")
        channels: number;
      }>;
      error?: string;
    }>("GET", "/agent/input-devices"),
```

- [ ] **Step 2: Verify typecheck**

Run: `cd web && node_modules/.bin/tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```
git add web/src/shared/api.ts
git commit -m "feat(web): api.listInputDevices for the new endpoint"
```

---

## Task 4: `select_input_device` gains DB-snapshot branch

**Files:**
- Modify: `packages/agent/src/lafufu_agent/audio_capture.py`
- Test: `packages/agent/tests/test_audio_capture.py`

Adds one new branch between the existing `LAFUFU_INPUT_DEVICE` env check and the PREFER scan. Reads a module-level `_db_input_device` that the agent service sets when the config snapshot arrives. `"auto"` falls through to the existing chain. Env always wins.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent/tests/test_audio_capture.py`:

```python
def test_db_input_device_setting_picks_named_match(monkeypatch):
    """When LAFUFU_INPUT_DEVICE env is unset and a DB snapshot has set
    agent.input_device to a name substring, the named device wins over
    PyAudio's reported default."""
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE", raising=False)
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_PREFER", raising=False)
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_AVOID", raising=False)

    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "Microsoft Sound Mapper - Input"},
        {"index": 1, "maxInputChannels": 4, "name": "Microphone Array (Realtek)"},
        {"index": 2, "maxInputChannels": 1, "name": "USB Mic"},
    ]
    p = _fake_pyaudio(devices, default_idx=0)

    audio_capture.set_db_input_device("usb")
    try:
        assert audio_capture.select_input_device(p) == 2
    finally:
        audio_capture.set_db_input_device("auto")  # reset for other tests


def test_db_input_device_numeric_index(monkeypatch):
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE", raising=False)
    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "A"},
        {"index": 1, "maxInputChannels": 4, "name": "B"},
    ]
    p = _fake_pyaudio(devices, default_idx=0)
    audio_capture.set_db_input_device("1")
    try:
        assert audio_capture.select_input_device(p) == 1
    finally:
        audio_capture.set_db_input_device("auto")


def test_db_input_device_auto_falls_through(monkeypatch):
    """`auto` is the sentinel — selector behaves identically to no DB hint."""
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE", raising=False)
    monkeypatch.setenv("LAFUFU_INPUT_DEVICE_PREFER", "shure")
    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "Generic"},
        {"index": 1, "maxInputChannels": 1, "name": "Shure SM7B"},
    ]
    p = _fake_pyaudio(devices, default_idx=0)
    audio_capture.set_db_input_device("auto")
    assert audio_capture.select_input_device(p) == 1


def test_env_var_beats_db_setting(monkeypatch):
    """LAFUFU_INPUT_DEVICE always wins — operator override is highest priority."""
    monkeypatch.setenv("LAFUFU_INPUT_DEVICE", "0")
    devices = [
        {"index": 0, "maxInputChannels": 1, "name": "A"},
        {"index": 1, "maxInputChannels": 1, "name": "B"},
    ]
    p = _fake_pyaudio(devices, default_idx=1)
    audio_capture.set_db_input_device("1")  # would otherwise win
    try:
        assert audio_capture.select_input_device(p) == 0
    finally:
        audio_capture.set_db_input_device("auto")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_audio_capture.py -v`
Expected: FAIL — `set_db_input_device` doesn't exist.

- [ ] **Step 3: Add the DB-snapshot branch + setter**

In `packages/agent/src/lafufu_agent/audio_capture.py`, locate the module-level `_selector_logged` flag and add a sibling:

```python
# Operator-set mic device from the DB snapshot (agent.input_device). Updated
# at runtime by AgentService when config.changed.agent.input_device fires.
# The literal "auto" means "fall through to the existing PREFER -> PyAudio
# default chain" — same behavior as not having set anything.
_db_input_device: str = "auto"


def set_db_input_device(value: str) -> None:
    """Set the DB-snapshot mic device. Called by AgentService on
    config.changed.agent.input_device."""
    global _db_input_device
    _db_input_device = (value or "auto").strip() or "auto"
```

Then inside `select_input_device`, **between** the `LAFUFU_INPUT_DEVICE` env block and the PREFER loop, insert this new branch:

```python
    if chosen is None and _db_input_device != "auto":
        db_value = _db_input_device
        if db_value.isdigit():
            idx = int(db_value)
            if any(i == idx for i, _ in devices):
                chosen = idx
                reason = f"agent.input_device={db_value}"
        else:
            needle = db_value.lower()
            for i, name in devices:
                if needle in name.lower():
                    chosen, reason = i, f"agent.input_device~={db_value!r} -> {name!r}"
                    break
```

Update the docstring `Selection order` section to match: insert `2. ``agent.input_device`` DB setting (if not "auto")` and renumber the rest.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_audio_capture.py -v`
Expected: PASS — all 8 tests (4 existing + 4 new) green.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/audio_capture.py packages/agent/tests/test_audio_capture.py
git commit -m "feat(agent): select_input_device honors DB-snapshot agent.input_device"
```

---

## Task 5: AgentService — `agent.input_device` subscriber

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Test: `packages/agent/tests/test_service.py`

Subscribes to `config.changed.agent.input_device`, calls `set_db_input_device`, and forces the mic to re-pick on next stream open by calling `self._mic.close()` if it has a `close()` method.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent/tests/test_service.py`:

```python
async def test_input_device_setting_resets_mic(nats_server):
    """A change to agent.input_device should both update the audio_capture
    module-level snapshot AND close the existing mic stream so the next
    listen rebinds to the new device."""
    from lafufu_agent import audio_capture

    closed = {"count": 0}

    class _Mic:
        def close(self):
            closed["count"] += 1

        def listen_once(self):
            return ""

    svc = AgentService(
        mic=_Mic(),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.input_device",
        schemas.ConfigChanged(key="agent.input_device", value="usb", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert audio_capture._db_input_device == "usb"
    assert closed["count"] >= 1, "mic should be closed so next listen picks the new device"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
    audio_capture.set_db_input_device("auto")  # reset for other tests
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_input_device_setting_resets_mic -v`
Expected: FAIL — no subscriber for `agent.input_device`.

- [ ] **Step 3: Add the subscriber + handler**

In `packages/agent/src/lafufu_agent/service.py`, locate the block of existing `nats_helper.subscribe_model` calls in `on_startup` (around the speaker / silence_threshold subscribers). Add this subscription alongside them:

```python
        # Mic device selection — operator can pick a specific input from the
        # admin UI. "auto" preserves the existing PREFER/PyAudio-default chain.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.input_device",
            schemas.ConfigChanged,
            self._on_config_input_device,
        )
```

Then add the handler method (place near the other `_on_config_*` handlers):

```python
    async def _on_config_input_device(self, subject: str, msg: schemas.ConfigChanged) -> None:
        from .audio_capture import set_db_input_device

        value = str(msg.value).strip() or "auto"
        set_db_input_device(value)
        self.log.info("agent.input_device.set value=%s", value)
        # Force the next listen to re-pick by closing the stream. _ensure_stream
        # reopens it bound to the new device.
        if hasattr(self._mic, "close"):
            try:
                self._mic.close()
            except Exception as e:
                self.log.warning("mic.close.failed_during_input_device_swap error=%s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_input_device_setting_resets_mic -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/tests/test_service.py
git commit -m "feat(agent): live-swap mic device via agent.input_device setting"
```

---

## Task 6: AgentService — `agent.interaction_mode` subscriber

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Test: `packages/agent/tests/test_service.py`

Updates `self._interaction_mode`. `_mic_loop` already dispatches on this field each iteration (see PR #11), so no restart is needed — the next iteration picks up the new mode.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent/tests/test_service.py`:

```python
async def test_interaction_mode_setting_swaps_field(nats_server):
    """Flipping agent.interaction_mode at runtime should update the field
    so the next _mic_loop iteration uses the new branch."""
    from lafufu_agent.trigger import InteractionMode

    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
    )
    assert svc._interaction_mode == InteractionMode.CONTINUOUS

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.interaction_mode",
        schemas.ConfigChanged(key="agent.interaction_mode", value="trigger", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert svc._interaction_mode == InteractionMode.TRIGGER

    # Invalid values should be rejected (logged + ignored), not crash
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.interaction_mode",
        schemas.ConfigChanged(key="agent.interaction_mode", value="bogus", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()
    assert svc._interaction_mode == InteractionMode.TRIGGER  # unchanged

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_interaction_mode_setting_swaps_field -v`
Expected: FAIL.

- [ ] **Step 3: Add the subscriber + handler**

In `on_startup`, add the subscription alongside the input-device one:

```python
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.interaction_mode",
            schemas.ConfigChanged,
            self._on_config_interaction_mode,
        )
```

Then add the handler:

```python
    async def _on_config_interaction_mode(
        self, subject: str, msg: schemas.ConfigChanged
    ) -> None:
        raw = str(msg.value).strip().lower()
        try:
            new_mode = InteractionMode(raw)
        except ValueError:
            self.log.warning("agent.interaction_mode.bad_value value=%r", msg.value)
            return
        if new_mode != self._interaction_mode:
            self.log.info(
                "agent.interaction_mode.set value=%s from=%s",
                new_mode.value,
                self._interaction_mode.value,
            )
            self._interaction_mode = new_mode
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_interaction_mode_setting_swaps_field -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/tests/test_service.py
git commit -m "feat(agent): live-swap agent.interaction_mode (continuous|trigger)"
```

---

## Task 7: AgentService — `agent.trigger.*` subscribers (5 handlers)

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Modify: `packages/agent/src/lafufu_agent/trigger.py` (extract validators so handlers can reject bad values without raising)
- Test: `packages/agent/tests/test_service.py`

5 settings, each with its own subscriber. Since `TriggerConfig` is frozen, each handler uses `dataclasses.replace` to produce a new instance with the changed field and reassigns `self._trigger`. The validation rules from `TriggerConfig.from_env` are duplicated as a helper so handlers can reject bad values without raising.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent/tests/test_service.py`:

```python
async def test_trigger_subscribers_mutate_config(nats_server):
    """Every agent.trigger.* setting should live-swap the corresponding field
    of svc._trigger via dataclasses.replace. Bad values get rejected, not
    crash the subscriber."""
    from lafufu_agent.trigger import TriggerConfig

    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        trigger_config=TriggerConfig.from_env({}),
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)

    async def push(key: str, value: str) -> None:
        await publish_model(
            nc,
            f"{topics.CONFIG_CHANGED}.{key}",
            schemas.ConfigChanged(key=key, value=value, source="test"),
        )
        await asyncio.sleep(0.15)

    await push("agent.trigger.phrase", "Speak your truth.")
    assert svc._trigger.phrase == "Speak your truth."

    await push("agent.trigger.emotion", "happy")
    assert svc._trigger.emotion == "happy"

    await push("agent.trigger.rounds", "3")
    assert svc._trigger.rounds == 3

    await push("agent.trigger.print_mode", "auto")
    assert svc._trigger.print_mode == "auto"

    await push("agent.trigger.print_prompt", "Want it on paper?")
    assert svc._trigger.print_prompt == "Want it on paper?"

    # Invalid values are rejected (logged + ignored) — config stays at last good value
    await push("agent.trigger.rounds", "0")
    assert svc._trigger.rounds == 3
    await push("agent.trigger.emotion", "drunk")
    assert svc._trigger.emotion == "happy"
    await push("agent.trigger.print_mode", "always")
    assert svc._trigger.print_mode == "auto"

    await nc.drain()
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_trigger_subscribers_mutate_config -v`
Expected: FAIL.

- [ ] **Step 3: Expose `TriggerConfig` field-validators on the module**

In `packages/agent/src/lafufu_agent/trigger.py`, expose the existing validation logic as small standalone helpers (extract from the bodies of `from_env`):

```python
def validate_emotion(value: str) -> str:
    """Raise ValueError if value isn't a known emotion. Returns the normalised value."""
    norm = value.strip().lower()
    if norm not in _VALID_EMOTIONS:
        raise ValueError(
            f"emotion={value!r} is not one of {sorted(_VALID_EMOTIONS)}"
        )
    return norm


def validate_print_mode(value: str) -> PrintMode:
    norm = value.strip().lower()
    if norm not in _PRINT_MODES:
        raise ValueError(f"print_mode={value!r} is not one of {list(_PRINT_MODES)}")
    return norm  # type: ignore[return-value]


def validate_rounds(value: str | int) -> int:
    n = int(value)
    if n < 1:
        raise ValueError(f"rounds={value!r} must be >= 1")
    return n
```

Then refactor `from_env` to call these helpers (the existing inline validation becomes a single call to each — no behavior change, just deduplication).

- [ ] **Step 4: Add the 5 subscribers + handlers**

In `on_startup`:

```python
        # Trigger-mode loop config — every field is live-tunable so the
        # admin UI can change wording/rounds/print behavior without restart.
        for field, handler in (
            ("agent.trigger.phrase", self._on_config_trigger_phrase),
            ("agent.trigger.emotion", self._on_config_trigger_emotion),
            ("agent.trigger.rounds", self._on_config_trigger_rounds),
            ("agent.trigger.print_mode", self._on_config_trigger_print_mode),
            ("agent.trigger.print_prompt", self._on_config_trigger_print_prompt),
        ):
            await nats_helper.subscribe_model(
                self.nats,
                f"{topics.CONFIG_CHANGED}.{field}",
                schemas.ConfigChanged,
                handler,
            )
```

The 5 handlers — each uses `dataclasses.replace` on the frozen config:

```python
    async def _on_config_trigger_phrase(self, subject, msg: schemas.ConfigChanged) -> None:
        import dataclasses

        self._trigger = dataclasses.replace(self._trigger, phrase=str(msg.value))
        self.log.info("agent.trigger.phrase.set len=%d", len(self._trigger.phrase))

    async def _on_config_trigger_emotion(self, subject, msg: schemas.ConfigChanged) -> None:
        import dataclasses

        from .trigger import validate_emotion

        try:
            value = validate_emotion(str(msg.value))
        except ValueError as e:
            self.log.warning("agent.trigger.emotion.bad_value %s", e)
            return
        self._trigger = dataclasses.replace(self._trigger, emotion=value)
        self.log.info("agent.trigger.emotion.set value=%s", value)

    async def _on_config_trigger_rounds(self, subject, msg: schemas.ConfigChanged) -> None:
        import dataclasses

        from .trigger import validate_rounds

        try:
            value = validate_rounds(msg.value)
        except (TypeError, ValueError) as e:
            self.log.warning("agent.trigger.rounds.bad_value %s", e)
            return
        self._trigger = dataclasses.replace(self._trigger, rounds=value)
        self.log.info("agent.trigger.rounds.set value=%d", value)

    async def _on_config_trigger_print_mode(self, subject, msg: schemas.ConfigChanged) -> None:
        import dataclasses

        from .trigger import validate_print_mode

        try:
            value = validate_print_mode(str(msg.value))
        except ValueError as e:
            self.log.warning("agent.trigger.print_mode.bad_value %s", e)
            return
        self._trigger = dataclasses.replace(self._trigger, print_mode=value)
        self.log.info("agent.trigger.print_mode.set value=%s", value)

    async def _on_config_trigger_print_prompt(self, subject, msg: schemas.ConfigChanged) -> None:
        import dataclasses

        self._trigger = dataclasses.replace(self._trigger, print_prompt=str(msg.value))
        self.log.info(
            "agent.trigger.print_prompt.set len=%d", len(self._trigger.print_prompt)
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_trigger_subscribers_mutate_config packages/agent/tests/test_trigger.py -v`
Expected: PASS. Make sure the existing trigger tests still pass after the validator extraction.

- [ ] **Step 6: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/src/lafufu_agent/trigger.py packages/agent/tests/test_service.py
git commit -m "feat(agent): live-swap all agent.trigger.* settings"
```

---

## Task 8: AgentService — `agent.wakeword.enabled` subscriber

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Modify: `packages/agent/src/lafufu_agent/__main__.py` (pass the constructed detector to AgentService so it can re-attach)
- Test: `packages/agent/tests/test_service.py`

Toggles `self._mic.wake_detector` between the constructed detector and `None`. The detector object is constructed at startup in `__main__.py` (gated by `LAFUFU_WAKEWORD_ENABLED=1` — that env var stays in charge of *whether the dep imports*, see the spec). We pass it to AgentService so the subscriber can re-attach on demand.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent/tests/test_service.py`:

```python
async def test_wakeword_enabled_toggles_detector_on_mic(nats_server):
    """agent.wakeword.enabled=true attaches the stored detector to the mic;
    false detaches it. Mic still works either way."""

    class _MicWithDetector:
        def __init__(self):
            self.wake_detector = None

        def listen_once(self):
            return ""

    fake_detector = object()  # any truthy sentinel; the mic just stores it
    mic = _MicWithDetector()

    svc = AgentService(
        mic=mic,
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        wake_detector=fake_detector,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.wakeword.enabled",
        schemas.ConfigChanged(key="agent.wakeword.enabled", value="true", source="test"),
    )
    await asyncio.sleep(0.2)
    assert mic.wake_detector is fake_detector

    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.wakeword.enabled",
        schemas.ConfigChanged(key="agent.wakeword.enabled", value="false", source="test"),
    )
    await asyncio.sleep(0.2)
    assert mic.wake_detector is None

    await nc.drain()
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_wakeword_enabled_toggles_detector_on_mic -v`
Expected: FAIL (`wake_detector` kwarg not accepted).

- [ ] **Step 3: Wire `wake_detector` through AgentService**

In `packages/agent/src/lafufu_agent/service.py`, add a `wake_detector=None` kwarg to `AgentService.__init__` and store it. Insert next to the `trigger_config` parameter:

```python
        wake_detector=None,
        ...
        # Constructed once in __main__.py (env-gated import). The
        # agent.wakeword.enabled setting controls whether it's currently
        # attached to the mic — we hold a stable reference here so the
        # enabled toggle can re-attach without reconstructing.
        self._wake_detector = wake_detector
```

Then add the subscription in `on_startup`:

```python
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.wakeword.enabled",
            schemas.ConfigChanged,
            self._on_config_wakeword_enabled,
        )
```

And the handler:

```python
    async def _on_config_wakeword_enabled(
        self, subject: str, msg: schemas.ConfigChanged
    ) -> None:
        v = msg.value
        if isinstance(v, str):
            enabled = v.strip().lower() in ("true", "1", "yes", "on")
        else:
            enabled = bool(v)

        if enabled and self._wake_detector is None:
            self.log.warning(
                "agent.wakeword.enabled=true but no detector was constructed at startup — "
                "set LAFUFU_WAKEWORD_ENABLED=1 in the service env and restart to import the dep"
            )
            return

        if not hasattr(self._mic, "wake_detector"):
            self.log.warning("mic has no wake_detector attribute — ignoring wakeword toggle")
            return

        self._mic.wake_detector = self._wake_detector if enabled else None
        self.log.info("agent.wakeword.enabled.set value=%s", enabled)
```

Update `__main__.py` to pass `wake_detector=wake_detector` to `AgentService(...)`. The constructed detector object is already on `wake_detector` in `main()` — just pass it through.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_wakeword_enabled_toggles_detector_on_mic -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/src/lafufu_agent/__main__.py packages/agent/tests/test_service.py
git commit -m "feat(agent): live-toggle agent.wakeword.enabled to attach/detach detector"
```

---

## Task 9: AgentService — `agent.wakeword.model` subscriber

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Modify: `packages/agent/src/lafufu_agent/__main__.py` (refactor wake-detector construction into a factory)
- Test: `packages/agent/tests/test_service.py`

Constructs a fresh `OpenWakeWordDetector` with the new name + the current threshold, calls `.load()`. On failure, log + keep the previous detector. On success, replace `self._wake_detector` AND `self._mic.wake_detector` (if currently attached). Skipped entirely if `self._wake_detector` is None (env-gated off).

- [ ] **Step 1: Write the failing test**

Append:

```python
async def test_wakeword_model_swap_replaces_detector(nats_server):
    """Changing the model name should construct a new OpenWakeWordDetector
    and attach it to the mic if the previous one was attached."""

    class _Detector:
        def __init__(self, name, threshold):
            self.model_name = name
            self.threshold = threshold
            self.loaded = False

        def load(self):
            self.loaded = True

    class _Mic:
        def __init__(self):
            self.wake_detector = None

        def listen_once(self):
            return ""

    mic = _Mic()
    initial = _Detector("hey_jarvis_v0.1", 0.5)
    mic.wake_detector = initial  # simulate enabled

    def detector_factory(name: str, threshold: float):
        d = _Detector(name, threshold)
        d.load()
        return d

    svc = AgentService(
        mic=mic,
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        wake_detector=initial,
        wake_detector_factory=detector_factory,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.wakeword.model",
        schemas.ConfigChanged(
            key="agent.wakeword.model", value="alexa_v0.1", source="test"
        ),
    )
    await asyncio.sleep(0.3)

    assert svc._wake_detector is not initial
    assert svc._wake_detector.model_name == "alexa_v0.1"
    assert svc._wake_detector.loaded
    assert mic.wake_detector is svc._wake_detector  # re-attached because was attached

    await nc.drain()
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_wakeword_model_swap_replaces_detector -v`
Expected: FAIL (`wake_detector_factory` kwarg not accepted).

- [ ] **Step 3: Implement**

In `service.py`, add the factory kwarg to `__init__`:

```python
        wake_detector_factory=None,  # callable(name: str, threshold: float) -> Detector
        ...
        self._wake_detector_factory = wake_detector_factory
```

Subscription in `on_startup`:

```python
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.wakeword.model",
            schemas.ConfigChanged,
            self._on_config_wakeword_model,
        )
```

Handler:

```python
    async def _on_config_wakeword_model(
        self, subject: str, msg: schemas.ConfigChanged
    ) -> None:
        if self._wake_detector_factory is None:
            self.log.warning(
                "agent.wakeword.model.set ignored — no detector factory configured "
                "(set LAFUFU_WAKEWORD_ENABLED=1 + restart to enable wakeword)"
            )
            return
        new_name = str(msg.value).strip()
        if not new_name:
            self.log.warning("agent.wakeword.model.empty_value")
            return
        previous = self._wake_detector
        previous_threshold = (
            getattr(previous, "threshold", 0.5) if previous is not None else 0.5
        )
        try:
            new_detector = self._wake_detector_factory(new_name, previous_threshold)
        except Exception as e:
            self.log.warning(
                "agent.wakeword.model.failed value=%s error=%s — keeping previous",
                new_name,
                e,
            )
            return

        currently_attached = (
            previous is not None
            and getattr(self._mic, "wake_detector", None) is previous
        )
        self._wake_detector = new_detector
        if currently_attached and hasattr(self._mic, "wake_detector"):
            self._mic.wake_detector = new_detector
        self.log.info("agent.wakeword.model.set value=%s", new_name)
```

Update `__main__.py` so the `main()` function constructs and passes a factory:

```python
        def make_wake_detector(name: str, threshold: float):
            from .wakeword import OpenWakeWordDetector

            d = OpenWakeWordDetector(model_name=name, threshold=threshold)
            d.load()
            return d

        # The initial detector is constructed once; the factory enables live
        # swaps via agent.wakeword.model.
        wake_detector = (
            make_wake_detector(
                os.environ.get("LAFUFU_WAKEWORD_MODEL", "hey_jarvis_v0.1"),
                float(os.environ.get("LAFUFU_WAKEWORD_THRESHOLD", "0.5")),
            )
            if os.environ.get("LAFUFU_WAKEWORD_ENABLED", "").lower() in ("1", "true", "yes")
            else None
        )

        # Then pass:
        # wake_detector=wake_detector,
        # wake_detector_factory=make_wake_detector,
```

Replace the existing wakeword-construction block in `__main__.py` with the new shape (the existing block constructs the detector directly; refactor to extract `make_wake_detector` and pass the factory).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_wakeword_model_swap_replaces_detector -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/src/lafufu_agent/__main__.py packages/agent/tests/test_service.py
git commit -m "feat(agent): live-swap agent.wakeword.model via detector factory"
```

---

## Task 10: AgentService — `agent.wakeword.threshold` subscriber

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Test: `packages/agent/tests/test_service.py`

Simplest of the wake-word handlers: mutates `self._wake_detector.threshold` directly. Clamps to `[0.0, 1.0]` belt-and-braces.

- [ ] **Step 1: Write the failing test**

Append:

```python
async def test_wakeword_threshold_setting_mutates_detector(nats_server):
    class _Detector:
        threshold = 0.5

    class _Mic:
        wake_detector = None

        def listen_once(self):
            return ""

    det = _Detector()
    mic = _Mic()
    mic.wake_detector = det

    svc = AgentService(
        mic=mic,
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        wake_detector=det,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.wakeword.threshold",
        schemas.ConfigChanged(
            key="agent.wakeword.threshold", value="0.3", source="test"
        ),
    )
    await asyncio.sleep(0.2)
    assert det.threshold == 0.3

    # Out-of-range clamps
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.wakeword.threshold",
        schemas.ConfigChanged(key="agent.wakeword.threshold", value="1.5", source="test"),
    )
    await asyncio.sleep(0.2)
    assert det.threshold == 1.0
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.wakeword.threshold",
        schemas.ConfigChanged(key="agent.wakeword.threshold", value="-0.5", source="test"),
    )
    await asyncio.sleep(0.2)
    assert det.threshold == 0.0

    await nc.drain()
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_wakeword_threshold_setting_mutates_detector -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Subscription in `on_startup`:

```python
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.wakeword.threshold",
            schemas.ConfigChanged,
            self._on_config_wakeword_threshold,
        )
```

Handler:

```python
    async def _on_config_wakeword_threshold(
        self, subject: str, msg: schemas.ConfigChanged
    ) -> None:
        try:
            v = float(msg.value)
        except (TypeError, ValueError):
            self.log.warning("agent.wakeword.threshold.bad_value value=%r", msg.value)
            return
        clamped = max(0.0, min(1.0, v))
        if self._wake_detector is None:
            self.log.info(
                "agent.wakeword.threshold.set value=%.3f (deferred — no detector)",
                clamped,
            )
            return
        self._wake_detector.threshold = clamped
        self.log.info("agent.wakeword.threshold.set value=%.3f", clamped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_wakeword_threshold_setting_mutates_detector -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/tests/test_service.py
git commit -m "feat(agent): live-tune agent.wakeword.threshold"
```

---

## Task 11: `_rebuild_tts` close-old-player fix

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Test: `packages/agent/tests/test_service.py`

PR #14 review flagged: when `_rebuild_tts` swaps player instances on a voice change, the old `_PyAudioPlayer` (Windows/macOS) is dropped without `close()`, leaking the WASAPI stream + PyAudio handle. Fix is 6 lines.

- [ ] **Step 1: Write the failing test**

Append:

```python
async def test_rebuild_tts_closes_old_player(nats_server):
    """When voice swap produces a new player, the old one's close() must
    be called so PyAudio output streams don't leak on Windows/macOS."""
    close_calls = {"count": 0}

    class _Player:
        def __init__(self, sr):
            self.sample_rate = sr

        def play(self, chunk):
            pass

        def end(self):
            pass

        def close(self):
            close_calls["count"] += 1

    from pathlib import Path

    def make_fake_piper(name: str) -> FakePiper:
        p = FakePiper()
        p.model_path = Path(f"/fake/{name}.onnx")
        p.voice_name = name
        # Different sample rate forces _rebuild_tts to construct a new player.
        p.sample_rate = 16000 if name == "voice_b" else 22050
        return p

    initial_piper = make_fake_piper("voice_a")
    initial_player = _Player(22050)

    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=initial_piper,
        speaker_play=initial_player,
        nats_url=nats_server,
        piper_factory=make_fake_piper,
        player_factory=lambda sr: _Player(sr),
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.voice_model",
        schemas.ConfigChanged(key="agent.voice_model", value="voice_b", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert close_calls["count"] == 1, (
        f"old player should be closed exactly once on voice swap; got {close_calls['count']}"
    )
    assert svc._speaker_play is not initial_player

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent/tests/test_service.py::test_rebuild_tts_closes_old_player -v`
Expected: FAIL (close was never called).

- [ ] **Step 3: Apply the fix in `_rebuild_tts`**

Locate the section of `_rebuild_tts` where the new player is assigned. The existing code looks like:

```python
        if old_rate != new_rate and self._player_factory is not None:
            self._speaker_play = self._player_factory(new_rate)
```

Replace with:

```python
        if old_rate != new_rate and self._player_factory is not None:
            old_player = self._speaker_play
            self._speaker_play = self._player_factory(new_rate)
            # _PyAudioPlayer (Windows/macOS dev path) holds an open output
            # stream + pyaudio.PyAudio() instance. _AplayPlayer and
            # _NoOpPlayer have no close() — hasattr guards both.
            if hasattr(old_player, "close"):
                try:
                    old_player.close()
                except Exception as e:
                    self.log.warning(
                        "speaker_play.close.failed_during_swap error=%s", e
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/agent/tests/test_service.py::test_rebuild_tts_closes_old_player packages/agent/tests/ -q`
Expected: PASS. All other agent tests stay green.

- [ ] **Step 5: Commit**

```
git add packages/agent/src/lafufu_agent/service.py packages/agent/tests/test_service.py
git commit -m "fix(agent): close old player before reassign in _rebuild_tts (PR #14 review)"
```

---

## Task 12: Frontend `DYNAMIC_OPTIONS` for new dropdowns

**Files:**
- Modify: `web/src/admin/settings_form.tsx`

Adds 5 new dropdown sources: 4 hardcoded enums + 1 endpoint-backed.

- [ ] **Step 1: Add the entries**

In `web/src/admin/settings_form.tsx`, locate the `DYNAMIC_OPTIONS` constant and append these entries (after the existing `agent.whisper_model` entry):

```typescript
  // Trigger mode + wake-word config — all enums hardcoded since these are
  // tiny fixed sets the backend doesn't enumerate.
  "agent.interaction_mode": async () => ["continuous", "trigger"],
  "agent.trigger.emotion": async () => [
    "happy", "sad", "angry", "surprised", "neutral", "agree", "disagree",
  ],
  "agent.trigger.print_mode": async () => ["none", "auto", "ask"],
  // openwakeword's bundled default models. When a custom hey_lafufu.onnx
  // lands in assets/wakeword/ (per the training scaffold), this should
  // switch to a /api/agent/wakeword-models endpoint that enumerates the
  // directory like /voices does.
  "agent.wakeword.model": async () => [
    "hey_jarvis_v0.1",
    "alexa_v0.1",
    "hey_mycroft_v0.1",
    "hey_rhasspy_v0.1",
    "timer_v0.1",
    "weather_v0.1",
  ],
  // Mic device picker — backend enumerates PyAudio's input devices,
  // first entry is always the "auto" sentinel.
  "agent.input_device": async () => {
    const { devices } = await api.listInputDevices();
    return devices.map((d) => ({ value: d.name, label: d.label }));
  },
```

- [ ] **Step 2: Verify typecheck + tests**

Run: `cd web && node_modules/.bin/tsc --noEmit && node_modules/.bin/vitest run`
Expected: tsc exit 0, vitest all 30 pass.

- [ ] **Step 3: Commit**

```
git add web/src/admin/settings_form.tsx
git commit -m "feat(web): dropdowns for new agent.* settings (trigger/wakeword/input)"
```

---

## Task 13: Frontend `SLIDER_HINTS` for new int/float settings

**Files:**
- Modify: `web/src/admin/settings_form.tsx`

Slider widget ranges for the 5 servo defaults + the 2 numeric agent settings. Per-servo ranges come straight from `packages/animator/src/lafufu_animator/pose.py`'s `CLAMP` table.

- [ ] **Step 1: Add the entries**

In `web/src/admin/settings_form.tsx`, locate the `SLIDER_HINTS` constant and append:

```typescript
  // Trigger / wake-word numeric tunables.
  "agent.trigger.rounds":     { min: 1,    max: 10,   step: 1     },
  "agent.wakeword.threshold": { min: 0.0,  max: 1.0,  step: 0.05  },
  // Servo defaults — ranges mirror packages/animator/.../pose.py CLAMP table.
  // Moving these sliders moves the robot LIVE — descriptions warn the operator.
  "animator.head_lr.default": { min: 1828, max: 2298, step: 1     },
  "animator.head_ud.default": { min: 2885, max: 3278, step: 1     },
  "animator.eye.default":     { min: 1960, max: 2130, step: 1     },
  "animator.jaw.default":     { min: 1534, max: 1728, step: 1     },
  "animator.brow.default":    { min: 2051, max: 2099, step: 1     },
```

- [ ] **Step 2: Verify build**

Run: `cd web && node_modules/.bin/tsc --noEmit && npm run build`
Expected: tsc + build clean.

- [ ] **Step 3: Commit**

```
git add web/src/admin/settings_form.tsx packages/control/src/lafufu_control/static/
git commit -m "feat(web): slider ranges for trigger.rounds, wakeword.threshold, servo defaults"
```

---

## Task 14: Tab reorg + sticky header

**Files:**
- Modify: `web/src/admin/settings_form.tsx`
- Create: `web/tests/settings_form.test.ts`

Service-aligned tabs (`agent | animator | audio | printer | other`); `categoryOf` reduces to one rule. Sticky CSS on the header row so tabs + search bar stay pinned while scrolling.

- [ ] **Step 1: Write the failing test**

Create `web/tests/settings_form.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { categoryOf } from "../src/admin/settings_form";

describe("categoryOf", () => {
  it("routes agent.* settings to the agent tab", () => {
    expect(categoryOf("agent.llm_model")).toBe("agent");
    expect(categoryOf("agent.system_prompt")).toBe("agent");
    expect(categoryOf("agent.voice_model")).toBe("agent");
    expect(categoryOf("agent.stt_backend")).toBe("agent");
    expect(categoryOf("agent.whisper_model")).toBe("agent");
    expect(categoryOf("agent.silence_threshold")).toBe("agent");
    expect(categoryOf("agent.silence_seconds")).toBe("agent");
    expect(categoryOf("agent.auto_listen")).toBe("agent");
    expect(categoryOf("agent.interaction_mode")).toBe("agent");
    expect(categoryOf("agent.trigger.phrase")).toBe("agent");
    expect(categoryOf("agent.wakeword.enabled")).toBe("agent");
    expect(categoryOf("agent.input_device")).toBe("agent");
  });

  it("routes animator.* settings to the animator tab", () => {
    expect(categoryOf("animator.idle_animation.enabled")).toBe("animator");
    expect(categoryOf("animator.head_lr.default")).toBe("animator");
    expect(categoryOf("animator.jaw.default")).toBe("animator");
  });

  it("routes speaker.* and tts.* to the audio tab", () => {
    expect(categoryOf("speaker.volume")).toBe("audio");
    expect(categoryOf("speaker.alsa_card")).toBe("audio");
    expect(categoryOf("tts.length_scale")).toBe("audio");
  });

  it("routes printer.* to the printer tab", () => {
    expect(categoryOf("printer.auto_print")).toBe("printer");
    expect(categoryOf("printer.media")).toBe("printer");
  });

  it("falls back to other for unknown prefixes", () => {
    expect(categoryOf("settings.bootstrap.no_new_settings")).toBe("other");
    expect(categoryOf("custom.foo")).toBe("other");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && node_modules/.bin/vitest run tests/settings_form.test.ts`
Expected: FAIL — `categoryOf` isn't exported, and several assertions fail.

- [ ] **Step 3: Rewrite `categoryOf` + `TABS` + `Tab` type**

In `web/src/admin/settings_form.tsx`:

Change the `Tab` type:

```typescript
type Tab = "agent" | "animator" | "audio" | "printer" | "other";
```

Replace the `TABS` array:

```typescript
const TABS: Array<{ id: Tab; label: string; hint: string }> = [
  { id: "agent",    label: "agent",    hint: "voice loop · trigger · wake word · mic" },
  { id: "animator", label: "animator", hint: "idle animation · servo defaults" },
  { id: "audio",    label: "audio",    hint: "speaker · TTS" },
  { id: "printer",  label: "printer",  hint: "auto-print · letterhead" },
  { id: "other",    label: "other",    hint: "uncategorised" },
];
```

Replace `categoryOf` and export it:

```typescript
export function categoryOf(key: string): Tab {
  // Audio tab is the one cross-namespace exception — speaker.* + tts.* both
  // live there because operators think of them together (volume + voice
  // playback speed).
  if (key.startsWith("speaker.") || key.startsWith("tts.")) return "audio";
  const prefix = key.split(".", 1)[0];
  if (prefix === "agent" || prefix === "animator" || prefix === "printer") {
    return prefix;
  }
  return "other";
}
```

- [ ] **Step 4: Add sticky CSS to the header row**

Locate the JSX that renders the tab row + search input (search for `<For each={TABS}>` and the filter `<input>` near it). Wrap the tab + search row in a div with sticky positioning. Identify the existing wrapper around them and add inline style:

```tsx
<div
  style={{
    position: "sticky",
    top: 0,
    "z-index": 1,
    background: "var(--c-shell, #1a1410)",
    "padding-bottom": "10px",
    "margin-bottom": "8px",
  }}
>
  {/* existing tab buttons + search input go here */}
</div>
```

The `var(--c-shell, #1a1410)` fallback matches the Panel chrome so sticky doesn't reveal scrolled content bleeding through.

- [ ] **Step 5: Run tests**

Run: `cd web && node_modules/.bin/tsc --noEmit && node_modules/.bin/vitest run`
Expected: all 31 tests pass (30 existing + new categoryOf suite).

- [ ] **Step 6: Manual smoke**

Rebuild + open the admin settings page:

Run: `cd web && npm run build`

In the browser, open `http://localhost:8080/admin`, click Settings. Confirm:
- Five tabs visible: agent / animator / audio / printer / other
- Search bar + tabs stay pinned at the top when scrolling a long list
- Clicking `agent` shows ~18 settings (existing 8 + 10 new)
- Clicking `animator` shows 6 settings (idle_animation.enabled + 5 servo defaults)

- [ ] **Step 7: Commit**

```
git add web/src/admin/settings_form.tsx web/tests/settings_form.test.ts packages/control/src/lafufu_control/static/
git commit -m "feat(web): service-aligned tabs + sticky header in settings panel"
```

---

## Task 15: Final verification + docs + PR

**Files:**
- Modify: `docs/local-dev.md`

- [ ] **Step 1: Full Python + ruff + web suite**

Run:
```
uv run pytest -q
uv run ruff check .
cd web && node_modules/.bin/tsc --noEmit && node_modules/.bin/vitest run
```

Expected: all green. Roughly 350+ pytest passing (was 333 + ~10 new agent + ~3 control), ruff clean, tsc 0, 31+ vitest passing.

- [ ] **Step 2: Add env-var to DB transition note to `docs/local-dev.md`**

Append a new section near the bottom of `docs/local-dev.md`:

```markdown
## Env vars vs DB settings

`bootstrap.py` seeds 24+ tunables into the control DB on first start. Most
operator-facing knobs (trigger mode, wake word, mic device, voice model,
LLM model, etc.) read from the DB at runtime via the `config.changed.*`
NATS subjects.

The `LAFUFU_*` env vars in agent / control service units act as
**first-boot seeds** only: their values populate the DB on a fresh
install via the bootstrap defaults. Once the rows exist, the env
vars are ignored — change settings via the admin UI instead.

Exceptions (always env-only):
- `LAFUFU_WAKEWORD_ENABLED` — controls whether `openwakeword` is
  imported at startup. Process-level decision; can't be a DB toggle.
- `LAFUFU_INPUT_DEVICE_PREFER` / `_AVOID` — per-host hardware lists;
  surfacing them as DB settings would require multi-host config.
- `LAFUFU_INPUT_DEVICE` — operator-level override; **always wins** over
  the `agent.input_device` DB setting.
```

- [ ] **Step 3: Push the branch**

Run:
```
git add docs/local-dev.md
git commit -m "docs: env-var vs DB-settings transition note"
git push -u origin feat/db-settings-migration
```

- [ ] **Step 4: Open PR**

Write a PR body to `/tmp/pr-body.md` (use the Write tool — heredocs trigger pre-commit security hooks):

```markdown
## Summary

Promotes 10 env-var-only knobs to DB-backed settings tunable from the admin UI on any device. Seeds 5 servo defaults that the animator already subscribes to but were never seeded. Reorganizes the settings form into service-aligned tabs (agent | animator | audio | printer | other) with a sticky header. Folds in the `_rebuild_tts` leak fix flagged in PR #14 review.

See `docs/superpowers/specs/2026-05-25-db-settings-migration-design.md` for the design.

## Test plan
- [x] `uv run pytest -q` — all green
- [x] `uv run ruff check .` — clean
- [x] `tsc --noEmit` + `vitest run` — clean
- [ ] On-device smoke: flip `agent.interaction_mode` to trigger via the admin UI, verify the next mic cycle waits for the wake word; flip back to continuous, verify RMS-onset returns.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Then create the PR:

```
gh pr create --base main --head feat/db-settings-migration --title "feat: DB-backed settings migration + service-aligned tabs" --body-file /tmp/pr-body.md
```
