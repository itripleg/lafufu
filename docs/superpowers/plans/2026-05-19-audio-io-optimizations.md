# Audio I/O Optimizations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut Lafufu's reply latency by streaming TTS instead of buffering it, eliminate first-utterance Whisper cold-start, remove unnecessary WAV file round-trip, add a faster-whisper backend selector matching the existing LLM-model selector UX, and fix two concurrency hazards in the mic loop.

**Architecture:** The agent's voice pipeline is `mic.listen_once() → ollama.chat() → piper.synthesize() → speaker.play()`. Today the second and third steps are blocking and fully-buffered, which is most of the perceived latency. This plan keeps the existing protocol/duck-typed structure (`MicProtocol`, `OllamaProtocol`, `PiperProtocol`) but adds streaming, async-offloading, and a pluggable STT backend (openai-whisper or faster-whisper) chosen by the same settings → NATS `config.changed.*` → live-switch pattern used for `agent.llm_model`.

**Tech Stack:** Python 3.13, pyaudio, openai-whisper, faster-whisper (CTranslate2), piper-tts, NATS, pytest, SolidJS admin UI, SQLModel settings table.

---

## Out of Scope

These were discussed during review but are NOT in this plan:

- **Barge-in** (user interrupts mid-reply): real-time VAD during TTS playback, cancellation of in-flight ollama+piper, audio ducking. Big architectural change — separate plan if pursued.
- **Replace aplay subprocess with persistent PyAudio output stream**: moderate refactor with its own underrun-tuning concerns. Can be a follow-up.
- **Whisper-model-name dropdown** (tiny/base/small/medium/large within a backend): the backend selector is the high-value lever; model size can be a text field for now and made into a dropdown later.

## Pre-Flight: Verify faster-whisper Installs on the Pi

Before any code work, confirm CTranslate2 wheels land cleanly on aarch64 Pi 5. If they don't, Task 1 needs to fall back to a different backend (vosk, whisper.cpp via Python bindings, etc.) and the plan needs revision.

```bash
ssh lafufu@lafufu '/srv/lafufu/.venv/bin/pip install --dry-run faster-whisper 2>&1 | tail -20'
```

Expected: dry-run resolves successfully with a CTranslate2 wheel for `linux_aarch64` (no source-build required). If it falls back to sdist + compile, allow ~5 min for the real install but it'll still work.

If this fails entirely, **stop and ask the operator** before continuing — the plan assumes faster-whisper as the second backend.

## File Structure

Files created or modified across all tasks. Grouped by responsibility.

| File | Status | Responsibility |
|---|---|---|
| `packages/agent/pyproject.toml` | modify | add `faster-whisper` dep |
| `packages/agent/src/lafufu_agent/stt.py` | rewrite | STT protocol + two backends (OpenAIWhisper, FasterWhisper) with `warmup()` + array-input `transcribe()` |
| `packages/agent/src/lafufu_agent/tts.py` | modify | streaming `synthesize_stream()` generator + keep sync `synthesize()` for tests |
| `packages/agent/src/lafufu_agent/pipeline.py` | modify | consume TTS as a stream; run Piper in executor |
| `packages/agent/src/lafufu_agent/__main__.py` | modify | construct STT from env+settings; pass sample rate to aplay; persistent mic stream |
| `packages/agent/src/lafufu_agent/service.py` | modify | warm STT in `on_startup`; subscribe to `agent.stt_backend` + `agent.whisper_model`; split mic-loop lock |
| `packages/agent/tests/test_stt.py` | create | unit tests for protocol + selector |
| `packages/agent/tests/test_pipeline.py` | modify | streaming test + executor test |
| `packages/agent/tests/test_service.py` | modify | warm-on-startup + lock-split test |
| `packages/control/src/lafufu_control/bootstrap.py` | modify | seed `agent.stt_backend`, `agent.whisper_model` |
| `packages/control/src/lafufu_control/api/routers/agent.py` | modify | `/api/agent/stt_backends` endpoint |
| `packages/control/tests/test_api_agent.py` | create | endpoint test |
| `packages/shared/src/lafufu_shared/testing.py` | modify | `FakePiper.synthesize_stream` + add `FakeSTT` with warmup |
| `web/src/shared/api.ts` | modify | `listSttBackends()` |
| `web/src/admin/settings_form.tsx` | modify | dynamic dropdown for `agent.stt_backend`; route key to "audio" tab |

---

## Task Ordering and Dependencies

Tasks build on each other in this order. Subagent dispatcher should execute serially unless noted:

1. **Task 1** — STT backend protocol + faster-whisper selector (foundation; everything else depends on this)
2. **Task 2** — Warm STT at startup (uses protocol from Task 1)
3. **Task 3** — Drop WAV file round-trip, pass arrays to STT (uses array-input method added in Task 1)
4. **Task 4** — Pass Piper sample rate to aplay (independent)
5. **Task 5** — Run Piper.synthesize in executor (independent, prepares Task 6)
6. **Task 6** — Stream Piper synthesis to playback (biggest TTFS win; builds on Task 5)
7. **Task 7** — Persistent mic stream across listen_once calls
8. **Task 8** — Release cycle_lock during silence-wait

---

## Task 1: STT Backend Protocol + faster-whisper Selector

**Goal:** Make `Whisper` a protocol with two concrete backends (`OpenAIWhisper`, `FasterWhisper`). Add `agent.stt_backend` + `agent.whisper_model` settings. Add `/api/agent/stt_backends` endpoint listing what's importable. Wire a live-switch subscriber. Add a dropdown in the admin UI populated dynamically (same pattern as `agent.llm_model`).

**Files:**
- Modify: `packages/agent/pyproject.toml` (add `faster-whisper>=1.0`)
- Modify: `packages/agent/src/lafufu_agent/stt.py`
- Modify: `packages/agent/src/lafufu_agent/__main__.py` (selection on boot)
- Modify: `packages/agent/src/lafufu_agent/service.py` (live-switch subscribers)
- Modify: `packages/control/src/lafufu_control/bootstrap.py` (seed settings)
- Modify: `packages/control/src/lafufu_control/api/routers/agent.py` (add endpoint)
- Modify: `packages/shared/src/lafufu_shared/testing.py` (FakeSTT with warmup)
- Modify: `web/src/shared/api.ts`
- Modify: `web/src/admin/settings_form.tsx`
- Test: `packages/agent/tests/test_stt.py` (create)
- Test: `packages/control/tests/test_api_agent.py` (create or extend)

### Task 1.1: Add faster-whisper dependency

- [ ] **Step 1: Add to pyproject**

Edit `packages/agent/pyproject.toml` `dependencies` list:

```toml
dependencies = [
    "lafufu-shared",
    "pyaudio>=0.2.14",
    "openai-whisper>=20231117",
    "faster-whisper>=1.0",
    "httpx>=0.27",
    "piper-tts>=1.2",
    "audioop-lts>=0.2.2",
]
```

- [ ] **Step 2: Lock + verify**

Run from repo root:

```bash
uv sync
```

Expected: lock updates, `faster-whisper` and `ctranslate2` resolve.

- [ ] **Step 3: Commit**

```bash
git add packages/agent/pyproject.toml uv.lock
git commit -m "deps(agent): add faster-whisper for STT backend selector"
```

### Task 1.2: Rewrite stt.py with backend protocol

- [ ] **Step 1: Write failing tests**

Create `packages/agent/tests/test_stt.py`:

```python
"""STT protocol + backend selector tests.

We can't import openai-whisper or faster-whisper in CI (heavy native deps),
so these tests use a fake backend to verify the selector + protocol shape,
plus light import-availability checks gated by pytest.importorskip.
"""
import numpy as np
import pytest

from lafufu_agent.stt import (
    SttProtocol,
    available_backends,
    make_stt,
)


def test_available_backends_returns_known_ids():
    """available_backends() reports which backends are importable."""
    avail = available_backends()
    # Both backend ids must always appear in the list, each with .available bool.
    ids = {b["id"] for b in avail}
    assert "openai-whisper" in ids
    assert "faster-whisper" in ids
    # Each entry has the shape the admin UI expects.
    for b in avail:
        assert set(b.keys()) >= {"id", "label", "available"}
        assert isinstance(b["available"], bool)


def test_make_stt_unknown_backend_falls_back_to_openai_whisper():
    """make_stt with an unknown id falls back to openai-whisper rather than crashing."""
    stt = make_stt("nonsense-backend", model_name="tiny")
    # Backend label survives the fallback so logs are honest.
    assert stt.backend_id == "openai-whisper"


def test_make_stt_respects_explicit_backend():
    """When backend is supported, make_stt picks it."""
    if not any(b["id"] == "faster-whisper" and b["available"] for b in available_backends()):
        pytest.skip("faster-whisper not installed")
    stt = make_stt("faster-whisper", model_name="tiny.en")
    assert stt.backend_id == "faster-whisper"


def test_stt_protocol_methods_exist():
    """Backends implement load(), warmup(), transcribe(array)."""
    stt = make_stt("openai-whisper", model_name="tiny")
    assert hasattr(stt, "load")
    assert hasattr(stt, "warmup")
    assert hasattr(stt, "transcribe")
    # transcribe accepts a numpy float32 array — no file path required.
    import inspect
    sig = inspect.signature(stt.transcribe)
    assert "audio" in sig.parameters
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest packages/agent/tests/test_stt.py -v
```

Expected: ImportError on `available_backends`, `make_stt`, `SttProtocol` from `lafufu_agent.stt`.

- [ ] **Step 3: Rewrite stt.py with protocol + two backends**

Replace `packages/agent/src/lafufu_agent/stt.py` with:

```python
"""STT backends with pluggable selector.

Two implementations:
  - OpenAIWhisper: the original openai-whisper package (CPU-friendly tiny.en).
  - FasterWhisper: CTranslate2-based reimplementation (~3-4x faster on aarch64).

Both implement the same protocol and accept numpy float32 audio (16kHz mono)
directly — no temp-file round-trip required.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class SttProtocol(Protocol):
    backend_id: str
    model_name: str

    def load(self) -> None: ...
    def warmup(self) -> float: ...
    def transcribe(self, audio: np.ndarray) -> str: ...


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def available_backends() -> list[dict]:
    """Report which backends are importable. Used by the admin /stt_backends endpoint."""
    return [
        {
            "id": "openai-whisper",
            "label": "openai-whisper (reference)",
            "available": _has_module("whisper"),
        },
        {
            "id": "faster-whisper",
            "label": "faster-whisper (CTranslate2, ~3-4x faster)",
            "available": _has_module("faster_whisper"),
        },
    ]


def make_stt(backend: str, model_name: str = "tiny") -> SttProtocol:
    """Build an STT instance for the given backend id.

    Unknown / unavailable backends fall back to openai-whisper so a broken
    setting can never brick the agent.
    """
    if backend == "faster-whisper" and _has_module("faster_whisper"):
        return FasterWhisper(model_name=model_name)
    if backend != "openai-whisper":
        log.warning("stt.backend.fallback requested=%s -> openai-whisper", backend)
    return OpenAIWhisper(model_name=model_name)


class OpenAIWhisper:
    """Reference openai-whisper backend. Accepts numpy float32 16kHz audio."""

    backend_id = "openai-whisper"

    def __init__(self, model_name: str = "tiny") -> None:
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        import whisper  # heavy import — lazy

        log.info("stt.load backend=%s model=%s", self.backend_id, self.model_name)
        self._model = whisper.load_model(self.model_name)

    def warmup(self) -> float:
        """Load + transcribe 0.5s of silence so the first real call is fast."""
        t0 = time.monotonic()
        self.load()
        silence = np.zeros(8000, dtype=np.float32)  # 0.5s @ 16kHz
        self._model.transcribe(silence, fp16=False, language="en", temperature=0.0)
        return time.monotonic() - t0

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32, mono, 16kHz."""
        if self._model is None:
            self.load()
        # Settings tuned to suppress "Thanks for watching!"-style hallucinations.
        result = self._model.transcribe(
            audio,
            fp16=False,
            language="en",
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            temperature=0.0,
        )
        return result.get("text", "").strip()


class FasterWhisper:
    """faster-whisper (CTranslate2) backend. Same interface as OpenAIWhisper."""

    backend_id = "faster-whisper"

    def __init__(self, model_name: str = "tiny.en") -> None:
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # heavy import — lazy

        log.info("stt.load backend=%s model=%s", self.backend_id, self.model_name)
        # int8 on CPU — small + fast, fine quality for tiny/base models.
        self._model = WhisperModel(
            self.model_name, device="cpu", compute_type="int8"
        )

    def warmup(self) -> float:
        t0 = time.monotonic()
        self.load()
        silence = np.zeros(8000, dtype=np.float32)
        # Consume the generator to actually run the model.
        for _ in self._model.transcribe(silence, language="en", beam_size=1)[0]:
            pass
        return time.monotonic() - t0

    def transcribe(self, audio: np.ndarray) -> str:
        if self._model is None:
            self.load()
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            temperature=0.0,
        )
        return "".join(s.text for s in segments).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest packages/agent/tests/test_stt.py -v
```

Expected: at least the three non-skipped tests PASS. `test_make_stt_respects_explicit_backend` may PASS or skip depending on faster-whisper install.

- [ ] **Step 5: Commit**

```bash
git add packages/agent/src/lafufu_agent/stt.py packages/agent/tests/test_stt.py
git commit -m "feat(stt): protocol + openai-whisper/faster-whisper backends with warmup"
```

### Task 1.3: Update FakeSTT and FakePiper in shared testing

The fakes need to grow so service-level tests can simulate warmup + streaming.

- [ ] **Step 1: Modify `packages/shared/src/lafufu_shared/testing.py`**

Replace the existing `FakeWhisper` class definition with:

```python
class FakeWhisper:
    """Maps canned audio identifiers to canned transcripts. Implements SttProtocol shape.

    Accepts either a numpy array (production interface) or a string (legacy
    test-only interface where callers pass an identifier).
    """

    backend_id = "fake"
    model_name = "fake"

    def __init__(self, mapping: dict[str, str] | None = None, fixed_reply: str = "") -> None:
        self.mapping = mapping or {}
        self.fixed_reply = fixed_reply
        self.calls: list = []
        self.warmup_count = 0
        self.load_count = 0

    def load(self) -> None:
        self.load_count += 1

    def warmup(self) -> float:
        self.warmup_count += 1
        return 0.0

    def transcribe(self, audio) -> str:
        self.calls.append(audio)
        if isinstance(audio, str):
            return self.mapping.get(audio, self.fixed_reply)
        # numpy array branch — just return the fixed reply
        return self.fixed_reply
```

- [ ] **Step 2: Run existing tests to make sure nothing broke**

```bash
uv run pytest packages/agent/tests/ -v -x
```

Expected: all existing tests pass (FakeWhisper backwards-compat for string input is preserved).

- [ ] **Step 3: Commit**

```bash
git add packages/shared/src/lafufu_shared/testing.py
git commit -m "test: FakeWhisper grows warmup/load + numpy transcribe to match new SttProtocol"
```

### Task 1.4: Seed settings + endpoint

- [ ] **Step 1: Write failing endpoint test**

Create `packages/control/tests/test_api_agent.py` (or extend if exists):

```python
"""Tests for /api/agent/* endpoints."""
from fastapi.testclient import TestClient

from lafufu_control.app import build_app


def _client():
    app = build_app()
    return TestClient(app)


def test_stt_backends_endpoint_returns_list():
    """GET /api/agent/stt_backends returns the available STT backends."""
    c = _client()
    r = c.get("/api/agent/stt_backends")
    assert r.status_code == 200
    body = r.json()
    assert "backends" in body
    ids = {b["id"] for b in body["backends"]}
    assert "openai-whisper" in ids
    assert "faster-whisper" in ids
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest packages/control/tests/test_api_agent.py -v
```

Expected: 404 on `/api/agent/stt_backends`.

- [ ] **Step 3: Add endpoint**

Append to `packages/control/src/lafufu_control/api/routers/agent.py`:

```python
@router.get("/stt_backends")
async def list_stt_backends(_: Request):
    """List installed STT backends.

    Used by the admin settings form to populate a dropdown for agent.stt_backend.
    Importing lafufu_agent here couples the control package to agent — that's
    acceptable because they ship in the same monorepo + venv.
    """
    from lafufu_agent.stt import available_backends

    return {"backends": available_backends()}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest packages/control/tests/test_api_agent.py -v
```

Expected: PASS.

- [ ] **Step 5: Seed defaults**

Modify `packages/control/src/lafufu_control/bootstrap.py` — add to `DEFAULTS` list, right after the `agent.llm_model` tuple:

```python
    (
        "agent.stt_backend",
        "openai-whisper",
        "str",
        "Speech-to-text backend. 'openai-whisper' is the reference; 'faster-whisper' is CTranslate2-based and ~3-4x faster on the Pi. Switch live from the admin UI.",
    ),
    (
        "agent.whisper_model",
        "tiny.en",
        "str",
        "STT model name. For openai-whisper: tiny/base/small/medium/large (or .en variants). For faster-whisper: same names work. Changing live forces a reload on the next utterance.",
    ),
```

- [ ] **Step 6: Commit**

```bash
git add packages/control/src/lafufu_control/api/routers/agent.py packages/control/src/lafufu_control/bootstrap.py packages/control/tests/test_api_agent.py
git commit -m "feat(control): /api/agent/stt_backends + seed agent.stt_backend, agent.whisper_model"
```

### Task 1.5: Wire selection in __main__ + live-switch in service

The agent should:
- On boot, read `LAFUFU_STT_BACKEND` and `LAFUFU_WHISPER_MODEL` env vars (these are settings → env via the snapshot bridge, so the systemd unit doesn't need to set them).
- Subscribe to `config.changed.agent.stt_backend` and `config.changed.agent.whisper_model` for live switching.

- [ ] **Step 1: Write failing service-level test**

Add to `packages/agent/tests/test_service.py`:

```python
async def test_stt_backend_change_swaps_stt_instance(nats_server):
    """When config.changed.agent.stt_backend fires, agent swaps the STT instance."""
    from lafufu_shared.testing import FakeWhisper

    initial = FakeWhisper(fixed_reply="initial")
    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        stt=initial,
        stt_factory=lambda backend, model: FakeWhisper(fixed_reply=f"{backend}:{model}"),
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.stt_backend",
        schemas.ConfigChanged(key="agent.stt_backend", value="faster-whisper"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    # AgentService.stt should now be a different FakeWhisper instance whose
    # fixed_reply encodes the new backend.
    assert svc.stt is not initial
    assert svc.stt.fixed_reply.startswith("faster-whisper:")

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest packages/agent/tests/test_service.py::test_stt_backend_change_swaps_stt_instance -v
```

Expected: AttributeError (AgentService doesn't accept `stt` or `stt_factory`).

- [ ] **Step 3: Extend AgentService**

Modify `packages/agent/src/lafufu_agent/service.py`:

In `__init__`, add `stt` and `stt_factory` params right before `nats_url`:

```python
    def __init__(
        self,
        mic,
        ollama,
        piper,
        speaker_play=None,
        nats_url: str | None = None,
        stt=None,
        stt_factory=None,
    ) -> None:
        super().__init__()
        self._mic = mic
        self._ollama = ollama
        self._piper = piper
        self._speaker_play = speaker_play
        self._nats_url = nats_url
        self.stt = stt
        # Callable (backend_id: str, model_name: str) -> SttProtocol. Used when
        # the live-switch subscriber needs to rebuild stt with a new backend.
        self._stt_factory = stt_factory
        self._stt_backend = "openai-whisper"
        self._stt_model = "tiny.en"
        self._pipeline: VoicePipeline | None = None
        self._cycle_lock = asyncio.Lock()
        self._mic_loop_task: asyncio.Task | None = None
        self._speaker_card = "USB"
        self._speaker_control = "PCM"
```

In `on_startup`, after the existing `agent.llm_model` subscriber and BEFORE `request_config_snapshot`, add:

```python
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.stt_backend",
            schemas.ConfigChanged,
            self._on_config_stt_backend,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.whisper_model",
            schemas.ConfigChanged,
            self._on_config_whisper_model,
        )
```

Add these handlers to the class (placement: near `_on_config_llm_model`):

```python
    async def _on_config_stt_backend(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_backend = str(msg.value).strip()
        if not new_backend or new_backend == self._stt_backend:
            return
        self._stt_backend = new_backend
        self._rebuild_stt(reason="backend")

    async def _on_config_whisper_model(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_model = str(msg.value).strip()
        if not new_model or new_model == self._stt_model:
            return
        self._stt_model = new_model
        self._rebuild_stt(reason="model")

    def _rebuild_stt(self, reason: str) -> None:
        if self._stt_factory is None:
            self.log.warning("stt.rebuild.skipped reason=%s factory_missing", reason)
            return
        prev = self.stt
        self.stt = self._stt_factory(self._stt_backend, self._stt_model)
        self.log.info(
            "stt.rebuilt reason=%s backend=%s model=%s prev=%r",
            reason, self._stt_backend, self._stt_model, type(prev).__name__,
        )
        # The mic is the consumer that calls stt.transcribe. Production RealMic
        # accepts a setter; if it does, propagate. Otherwise the next listen_once
        # will fail loudly and the operator will notice.
        if hasattr(self._mic, "set_stt"):
            self._mic.set_stt(self.stt)
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest packages/agent/tests/test_service.py::test_stt_backend_change_swaps_stt_instance -v
```

Expected: PASS.

- [ ] **Step 5: Update RealMic and __main__ to use the new STT protocol**

Modify `packages/agent/src/lafufu_agent/__main__.py`:

Replace the `RealMic` `__init__` to accept an `SttProtocol` instead of a `Whisper`-typed param, and add `set_stt`:

```python
class RealMic:
    PRE_ROLL_S = 0.35
    MAX_RECORD_S = 10.0
    MAX_WAIT_S = 30.0
    MIN_VOICED_CHUNKS = 5

    def __init__(
        self,
        stt,
        *,
        rate: int = 44100,
        chunk_ms: int = 40,
        silence_threshold: int = 800,
        silence_tail_s: float = 1.5,
    ):
        self.stt = stt
        self.rate = rate
        self.chunk_size = int(rate * chunk_ms / 1000)
        self.tmp_wav = Path("/tmp/lafufu_capture.wav")  # still used until Task 3
        self.silence_threshold = silence_threshold
        self.silence_tail_s = silence_tail_s

    def set_stt(self, stt) -> None:
        """Hot-swap STT instance (called by AgentService on config.changed)."""
        self.stt = stt
```

Update the `listen_once` last lines to use `self.stt.transcribe`:

```python
        # ... (existing recording loop unchanged) ...
        with wave.open(str(self.tmp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(raw)
        return self.stt.transcribe(self.tmp_wav)  # Task 3 will change this to numpy
```

Wait — `transcribe` now takes a numpy array per the new protocol, but Task 3 will switch RealMic to numpy. For now (so this task lands cleanly), keep both interfaces working: OpenAIWhisper's `transcribe()` accepts a path OR an array via whisper's native polymorphism. **Add a path-accepting branch** to both backends just for this transition task:

In `stt.py`, modify `OpenAIWhisper.transcribe`:

```python
    def transcribe(self, audio) -> str:
        """audio: float32 numpy array (mono 16kHz) OR a file path."""
        if self._model is None:
            self.load()
        result = self._model.transcribe(
            audio if not isinstance(audio, (str, Path)) else str(audio),
            fp16=False,
            language="en",
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            temperature=0.0,
        )
        return result.get("text", "").strip()
```

And import `Path` at the top of stt.py:

```python
from pathlib import Path
```

For `FasterWhisper.transcribe`, do the same:

```python
    def transcribe(self, audio) -> str:
        if self._model is None:
            self.load()
        # faster-whisper accepts numpy array OR file path natively.
        target = audio if not isinstance(audio, (str, Path)) else str(audio)
        segments, _info = self._model.transcribe(
            target,
            language="en",
            beam_size=1,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            temperature=0.0,
        )
        return "".join(s.text for s in segments).strip()
```

Modify the `main()` function in `__main__.py`:

```python
def main() -> None:
    from .stt import make_stt

    whisper_model = os.environ.get("LAFUFU_WHISPER_MODEL", "tiny.en")
    stt_backend = os.environ.get("LAFUFU_STT_BACKEND", "openai-whisper")
    qwen_model = os.environ.get("LAFUFU_LLM_MODEL", "qwen2.5:7b")
    piper_model_path = Path(os.environ.get("LAFUFU_PIPER_MODEL", "models/lafufu_voice.onnx"))
    ollama_url = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

    stt = make_stt(stt_backend, model_name=whisper_model)
    ollama = Ollama(base_url=ollama_url, model=qwen_model, system_prompt=SYSTEM_PROMPT)
    piper = Piper(model_path=piper_model_path)
    mic = RealMic(stt=stt)
    player = _aplay_player()

    svc = AgentService(
        mic=mic,
        ollama=ollama,
        piper=piper,
        speaker_play=player,
        stt=stt,
        stt_factory=lambda backend, model: make_stt(backend, model_name=model),
    )

    asyncio.run(svc.run())
```

Also remove the now-unused `Whisper` import at the top — replace `from .stt import Whisper` with nothing (it's imported lazily inside main now).

- [ ] **Step 6: Run all agent tests**

```bash
uv run pytest packages/agent/tests/ -v
```

Expected: all green. If any existing test referenced `Whisper` by name, swap to `make_stt`.

- [ ] **Step 7: Update systemd to expose new env vars** (optional, settings snapshot also sets them)

Modify `deploy/systemd/lafufu-agent.service` — change line 14:

```ini
Environment=LAFUFU_WHISPER_MODEL=tiny.en
Environment=LAFUFU_STT_BACKEND=openai-whisper
```

(These are just boot defaults; the DB settings + snapshot mechanism will override them at runtime if set.)

- [ ] **Step 8: Commit**

```bash
git add packages/agent/src/lafufu_agent/stt.py packages/agent/src/lafufu_agent/service.py packages/agent/src/lafufu_agent/__main__.py packages/agent/tests/test_service.py deploy/systemd/lafufu-agent.service
git commit -m "feat(agent): live-switch STT backend + whisper model via config.changed"
```

### Task 1.6: Admin UI dropdown

- [ ] **Step 1: Add API method**

Modify `web/src/shared/api.ts` — add right after `listLlmModels`:

```typescript
  listSttBackends: () =>
    req<{ backends: Array<{ id: string; label: string; available: boolean }> }>(
      "GET",
      "/agent/stt_backends",
    ),
```

- [ ] **Step 2: Add to DYNAMIC_OPTIONS**

Modify `web/src/admin/settings_form.tsx`:

In the `DYNAMIC_OPTIONS` map (around line 33), add:

```typescript
const DYNAMIC_OPTIONS: Record<string, () => Promise<string[]>> = {
  "agent.llm_model": async () => {
    const { models } = await api.listLlmModels();
    return models.map((m) => m.name);
  },
  "agent.stt_backend": async () => {
    const { backends } = await api.listSttBackends();
    // Only list backends that are actually installed.
    return backends.filter((b) => b.available).map((b) => b.id);
  },
};
```

In `categoryOf` (around line 393), add `agent.stt_backend` and `agent.whisper_model` to the audio tab:

```typescript
function categoryOf(key: string): Tab {
  if (key === "agent.llm_model" || key === "agent.system_prompt") return "model";
  if (key.startsWith("printer.")) return "printer";
  if (
    key.startsWith("speaker.") ||
    key.startsWith("tts.") ||
    key === "agent.silence_threshold" ||
    key === "agent.silence_seconds" ||
    key === "agent.auto_listen" ||
    key === "agent.stt_backend" ||
    key === "agent.whisper_model"
  ) return "audio";
  return "other";
}
```

- [ ] **Step 3: Build frontend + smoke**

```bash
cd web
pnpm install   # if not already
pnpm build
```

Expected: build succeeds; new bundle hashes in `packages/control/src/lafufu_control/static/assets/`.

- [ ] **Step 4: Commit**

```bash
git add web/src/shared/api.ts web/src/admin/settings_form.tsx packages/control/src/lafufu_control/static/
git commit -m "feat(admin): STT backend dropdown wired to /api/agent/stt_backends"
```

---

## Task 2: Warm STT at Startup

**Goal:** Don't pay 2-5s model-load on the first utterance — warm Whisper in `on_startup` like Ollama already is.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Modify: `packages/agent/tests/test_service.py`

- [ ] **Step 1: Write failing test**

Append to `packages/agent/tests/test_service.py`:

```python
async def test_on_startup_warms_stt(nats_server):
    """AgentService.on_startup() should call stt.warmup() so first utterance is fast."""
    from lafufu_shared.testing import FakeWhisper

    fake_stt = FakeWhisper()
    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        stt=fake_stt,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    assert fake_stt.warmup_count == 1, "stt.warmup() should be called once during on_startup"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest packages/agent/tests/test_service.py::test_on_startup_warms_stt -v
```

Expected: FAIL — `warmup_count == 0`.

- [ ] **Step 3: Add warmup call**

Modify `packages/agent/src/lafufu_agent/service.py` — in `on_startup`, right after the Ollama warmup block (around line 57-62) and BEFORE constructing `VoicePipeline`:

```python
        # Hot-warm STT in an executor — same idea as Ollama warmup. Done off
        # the loop because whisper.load_model + a 0.5s dummy decode is blocking
        # C code that would freeze NATS subscribers otherwise.
        if self.stt is not None and hasattr(self.stt, "warmup"):
            try:
                loop = asyncio.get_running_loop()
                elapsed = await loop.run_in_executor(None, self.stt.warmup)
                self.log.info("stt.warmed_up backend=%s elapsed_s=%.1f",
                              getattr(self.stt, "backend_id", "?"), elapsed)
            except Exception as e:
                self.log.warning("stt.warmup.failed error=%s", e)
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest packages/agent/tests/test_service.py::test_on_startup_warms_stt -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent/src/lafufu_agent/service.py packages/agent/tests/test_service.py
git commit -m "perf(agent): warm STT on startup so first utterance doesn't pay model-load"
```

---

## Task 3: Drop WAV File Round-Trip

**Goal:** Pass numpy float32 audio directly to STT instead of writing `/tmp/lafufu_capture.wav` and reading it back inside Whisper. Saves ~20-30ms per cycle and ditches a disk write (even on tmpfs).

**Files:**
- Modify: `packages/agent/src/lafufu_agent/__main__.py`
- Test: existing `test_service.py` still passes (no test code changes needed, but verify)

- [ ] **Step 1: Modify `RealMic.listen_once` to skip the WAV step**

In `packages/agent/src/lafufu_agent/__main__.py`, replace the `finally`-onward block (~lines 133-150) with:

```python
        finally:
            stream.stop_stream()
            stream.close()

        if not started or not frames:
            return ""

        import audioop

        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)

        # Convert int16 PCM bytes → float32 numpy array normalized to [-1, 1].
        # Both STT backends accept this directly, skipping a disk write + decode.
        import numpy as np
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self.stt.transcribe(audio_np)
```

Also remove `self.tmp_wav = Path("/tmp/lafufu_capture.wav")` from `RealMic.__init__` and the `wave` import at the top of the file (if no other code uses it).

- [ ] **Step 2: Run existing tests**

```bash
uv run pytest packages/agent/tests/ -v
```

Expected: all pass (RealMic isn't exercised by unit tests directly — its callers use FakeMic — but service-level integration still passes).

- [ ] **Step 3: Manual verification note**

Add a TODO to the deploy doc reminding the operator that the first deploy of this change should be smoke-tested by speaking into the mic and checking that the transcript matches in the admin UI (`/admin` → pulse view). This is genuinely Pi-only behavior.

- [ ] **Step 4: Commit**

```bash
git add packages/agent/src/lafufu_agent/__main__.py
git commit -m "perf(mic): pass float32 numpy to STT instead of WAV file round-trip"
```

---

## Task 4: Pass Piper Sample Rate to aplay

**Goal:** Replace the hardcoded `22050` in the aplay invocation with the actual sample rate of the loaded Piper voice. Future-proofs against voice swaps to 16k or 24k models.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/__main__.py`
- Modify: `packages/agent/tests/test_pipeline.py` (add test)

- [ ] **Step 1: Write failing test**

Append to `packages/agent/tests/test_pipeline.py`:

```python
def test_aplay_player_uses_dynamic_sample_rate(monkeypatch):
    """_AplayPlayer should invoke aplay with the rate it was constructed with."""
    from lafufu_agent.__main__ import _AplayPlayer

    invocations: list[list[str]] = []

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            invocations.append(argv)
            self.stdin = type("S", (), {"write": lambda self, b: None,
                                         "flush": lambda self: None,
                                         "close": lambda self: None})()
        def poll(self): return None

    import subprocess as _sp
    monkeypatch.setattr(_sp, "Popen", _FakePopen)

    player = _AplayPlayer(sample_rate=16000)
    player.play(b"\x00\x00" * 100)
    assert any("16000" in argv for argv in invocations), \
        f"aplay must use the passed sample rate; got {invocations}"
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest packages/agent/tests/test_pipeline.py::test_aplay_player_uses_dynamic_sample_rate -v
```

Expected: FAIL — `_AplayPlayer.__init__` doesn't accept `sample_rate`.

- [ ] **Step 3: Modify `_AplayPlayer`**

Replace `_AplayPlayer` in `packages/agent/src/lafufu_agent/__main__.py`:

```python
class _AplayPlayer:
    """Per-utterance aplay subprocess.

    Sample rate is set at construction so the aplay invocation matches the
    Piper voice's native rate (no resample, no pitch shift).
    """

    def __init__(self, sample_rate: int = 22050) -> None:
        import subprocess
        self._subprocess = subprocess
        self._proc: subprocess.Popen | None = None
        self._sample_rate = int(sample_rate)
        # Buffer ~2s, period ~0.2s, scaled to sample rate.
        self._buffer_size = self._sample_rate * 2
        self._period_size = self._sample_rate // 5

    def play(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._proc is None or self._proc.poll() is not None:
            device = os.environ.get("LAFUFU_APLAY_DEVICE", "default")
            self._proc = self._subprocess.Popen(
                [
                    "aplay", "-q",
                    "-D", device,
                    "-f", "S16_LE",
                    "-c", "1",
                    "-r", str(self._sample_rate),
                    f"--buffer-size={self._buffer_size}",
                    f"--period-size={self._period_size}",
                ],
                stdin=self._subprocess.PIPE,
            )
        try:
            self._proc.stdin.write(chunk)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            self._proc = None

    def end(self) -> None:
        if self._proc is None:
            return
        try:
            # ~100ms of silence so the last buffer flush carries through.
            silence_frames = self._sample_rate // 10
            self._proc.stdin.write(b"\x00\x00" * silence_frames)
            self._proc.stdin.flush()
            self._proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        self._proc = None
```

- [ ] **Step 4: Update `main()` to pass `piper.sample_rate`**

In `main()` in the same file, change:

```python
    player = _aplay_player()
```

to:

```python
    piper.load()  # populate sample_rate from the .onnx config
    player = _AplayPlayer(sample_rate=piper.sample_rate)
```

And delete the `_aplay_player()` helper function (no longer used).

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest packages/agent/tests/test_pipeline.py::test_aplay_player_uses_dynamic_sample_rate -v
uv run pytest packages/agent/tests/ -v
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/agent/src/lafufu_agent/__main__.py packages/agent/tests/test_pipeline.py
git commit -m "fix(audio): aplay uses Piper voice's native sample rate, not hardcoded 22050"
```

---

## Task 5: Run Piper.synthesize in an Executor

**Goal:** Piper synth is a blocking C call. Right now it freezes the asyncio event loop for the entire synth duration. Wrap it in `run_in_executor` so NATS subscribers and config changes keep flowing during synth.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/pipeline.py`
- Modify: `packages/agent/tests/test_pipeline.py`

- [ ] **Step 1: Write failing test**

Append to `packages/agent/tests/test_pipeline.py`:

```python
async def test_pipeline_does_not_block_event_loop_during_synth(nats_server):
    """While Piper is synthesizing, the event loop must still process callbacks."""
    import nats
    import time as _time

    class _SlowPiper:
        sample_rate = 22050
        chunk_ms = 40
        def synthesize(self, text):
            # 200ms blocking call simulating a slow synth.
            _time.sleep(0.2)
            return [(b"\x00" * 1764, 0.0)]

    nc = await nats.connect(nats_server)
    pipeline = VoicePipeline(
        nats_client=nc,
        mic=FakeMic(),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=_SlowPiper(),
    )

    # Schedule a heartbeat that should fire DURING synth.
    ticks: list[float] = []
    async def heartbeat():
        for _ in range(8):
            ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.03)

    hb = asyncio.create_task(heartbeat())
    await pipeline.run_one_cycle()
    await hb
    await nc.drain()

    # All 8 ticks should be there even though synth blocked for 200ms.
    assert len(ticks) == 8, f"event loop starved during synth; got {len(ticks)} ticks"
    # Ticks should be ~30ms apart, not all bunched after the 200ms block.
    spans = [ticks[i+1] - ticks[i] for i in range(len(ticks)-1)]
    assert max(spans) < 0.18, f"event loop blocked for >180ms during synth: {spans}"
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest packages/agent/tests/test_pipeline.py::test_pipeline_does_not_block_event_loop_during_synth -v
```

Expected: FAIL — one tick gap will be ~200ms because synth is in-line.

- [ ] **Step 3: Run synth in executor**

In `packages/agent/src/lafufu_agent/pipeline.py`, modify `speak()`:

Replace the line `chunks = self.piper.synthesize(text)` (around line 86) with:

```python
        loop = asyncio.get_running_loop()
        chunks = await loop.run_in_executor(None, self.piper.synthesize, text)
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest packages/agent/tests/test_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent/src/lafufu_agent/pipeline.py packages/agent/tests/test_pipeline.py
git commit -m "perf(pipeline): run Piper synth in executor so event loop stays responsive"
```

---

## Task 6: Stream Piper Synthesis to Playback

**Goal:** Today `Piper.synthesize()` joins ALL chunks then returns a list — playback can't start until synthesis finishes. Stream it: yield rechunked frames as Piper produces them, so the speaker emits the first phoneme ~200ms after the synth begins instead of ~1500ms.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/tts.py`
- Modify: `packages/agent/src/lafufu_agent/pipeline.py`
- Modify: `packages/shared/src/lafufu_shared/testing.py` (FakePiper streams too)
- Modify: `packages/agent/tests/test_pipeline.py`

### Task 6.1: Add streaming generator to Piper

- [ ] **Step 1: Write failing test**

Add to `packages/agent/tests/test_pipeline.py`:

```python
def test_fake_piper_supports_streaming_iteration():
    """FakePiper.synthesize_stream yields chunks one at a time."""
    from lafufu_shared.testing import FakePiper

    fp = FakePiper(chunks=[(b"\x00" * 100, 0.1), (b"\x00" * 100, 0.2)])
    streamed = list(fp.synthesize_stream("hello"))
    assert streamed == [(b"\x00" * 100, 0.1), (b"\x00" * 100, 0.2)]
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest packages/agent/tests/test_pipeline.py::test_fake_piper_supports_streaming_iteration -v
```

Expected: AttributeError — no `synthesize_stream`.

- [ ] **Step 3: Add `synthesize_stream` to FakePiper**

In `packages/shared/src/lafufu_shared/testing.py`, add inside `FakePiper`:

```python
    sample_rate = 22050
    chunk_ms = 40

    def synthesize_stream(self, text: str):
        """Yield canned chunks one at a time (matches real Piper streaming shape)."""
        self.calls.append(text)
        yield from self.chunks
```

- [ ] **Step 4: Add `synthesize_stream` to real Piper**

In `packages/agent/src/lafufu_agent/tts.py`, replace the entire `Piper` class with:

```python
"""Piper TTS wrapper.

Two APIs:
  synthesize(text)         → list of (chunk_bytes, mouth_target_0to1) — buffered
  synthesize_stream(text)  → generator yielding the same tuples as Piper produces
                             them, for low-latency streaming playback
"""

import logging
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger(__name__)


class Piper:
    def __init__(self, model_path: Path, chunk_ms: int = 40) -> None:
        self.model_path = Path(model_path)
        self.chunk_ms = chunk_ms
        self._voice = None
        self._sample_rate = 22050
        self._sample_width = 2

    def load(self) -> None:
        if self._voice is not None:
            return
        from piper import PiperVoice

        self._voice = PiperVoice.load(str(self.model_path))
        self._sample_rate = self._voice.config.sample_rate

    def synthesize(self, text: str) -> list[tuple[bytes, float]]:
        """Buffered: join all audio, rechunk, return list. Used by tests + legacy callers."""
        return list(self.synthesize_stream(text))

    def synthesize_stream(self, text: str) -> Iterator[tuple[bytes, float]]:
        """Stream: yield (chunk, mouth_target) tuples as Piper synthesizes.

        Buffers across Piper's internal chunk boundaries so emitted chunks are
        all exactly `chunk_ms` long (the animator depends on a steady cadence).
        The final partial chunk is yielded as-is.
        """
        if self._voice is None:
            self.load()

        import audioop

        bytes_per_sample = self._sample_width
        samples_per_chunk = int(self._sample_rate * self.chunk_ms / 1000)
        bytes_per_chunk = samples_per_chunk * bytes_per_sample

        buf = bytearray()
        for piper_chunk in self._voice.synthesize(text):
            buf.extend(piper_chunk.audio_int16_bytes)
            while len(buf) >= bytes_per_chunk:
                out = bytes(buf[:bytes_per_chunk])
                del buf[:bytes_per_chunk]
                rms = audioop.rms(out, bytes_per_sample)
                yield out, min(1.0, rms / 8000.0)
        if buf:
            tail = bytes(buf)
            rms = audioop.rms(tail, bytes_per_sample)
            yield tail, min(1.0, rms / 8000.0)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def sample_width(self) -> int:
        return self._sample_width
```

- [ ] **Step 5: Run to verify**

```bash
uv run pytest packages/agent/tests/test_pipeline.py::test_fake_piper_supports_streaming_iteration -v
```

Expected: PASS.

### Task 6.2: Pipeline consumes the stream

- [ ] **Step 1: Write failing test**

Add to `packages/agent/tests/test_pipeline.py`:

```python
async def test_pipeline_streams_first_chunk_before_synth_finishes(nats_server):
    """TTFS test: first chunk should hit the speaker before the last chunk is synthesized."""
    import nats
    import time as _time

    play_times: list[float] = []

    class _StreamingSlowPiper:
        sample_rate = 22050
        chunk_ms = 40
        def synthesize_stream(self, text):
            for i in range(5):
                _time.sleep(0.1)  # 100ms per chunk
                yield (b"\x00" * 1764, 0.5)
        def synthesize(self, text):
            return list(self.synthesize_stream(text))

    def _record_play(chunk):
        play_times.append(_time.monotonic())

    nc = await nats.connect(nats_server)
    pipeline = VoicePipeline(
        nats_client=nc,
        mic=FakeMic(),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=_StreamingSlowPiper(),
        speaker_play=_record_play,
    )

    t_start = _time.monotonic()
    await pipeline.run_one_cycle()
    await nc.drain()

    assert len(play_times) == 5
    # First chunk should arrive within ~250ms of synth start (first synth chunk
    # is ~100ms, plus loop overhead). NOT 500ms (which would mean buffering).
    first_chunk_latency = play_times[0] - t_start
    assert first_chunk_latency < 0.25, f"first chunk latency {first_chunk_latency:.3f}s exceeds budget — synth was buffered"
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest packages/agent/tests/test_pipeline.py::test_pipeline_streams_first_chunk_before_synth_finishes -v
```

Expected: FAIL — first chunk arrives at ~500ms because pipeline still buffers.

- [ ] **Step 3: Modify `VoicePipeline.speak()` to consume the stream**

In `packages/agent/src/lafufu_agent/pipeline.py`, replace the existing `speak()` method body from `chunks = ...` onwards with:

```python
    async def speak(self, text: str, emotion: str = "neutral", source: str = "llm") -> None:
        await nats_helper.publish_model(
            self.nats,
            topics.AGENT_REPLY,
            schemas.AgentReply(text=text, emotion=emotion, source=source),  # type: ignore[arg-type]
        )
        await self._publish_state("speaking")

        play_fn = (
            self.speaker_play.play
            if self.speaker_play and hasattr(self.speaker_play, "play")
            else self.speaker_play
        )

        chunk_dt = getattr(self.piper, "chunk_ms", 40) / 1000.0
        start_ts = time.monotonic()
        next_tick = time.monotonic()

        # Stream synth in an executor — we pull chunks one at a time via a
        # bounded queue so blocking generator iteration doesn't freeze the loop.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[bytes, float] | None] = asyncio.Queue(maxsize=8)

        def _produce():
            try:
                gen = self.piper.synthesize_stream(text) \
                    if hasattr(self.piper, "synthesize_stream") \
                    else iter(self.piper.synthesize(text))
                for item in gen:
                    asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        producer_fut = loop.run_in_executor(None, _produce)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                audio_bytes, mouth_target = item
                if play_fn:
                    play_fn(audio_bytes)
                await nats_helper.publish_model(
                    self.nats,
                    topics.AGENT_TTS_RMS,
                    schemas.AgentTtsRms(
                        ts=time.monotonic() - start_ts,
                        rms=mouth_target,
                        mouth_target=mouth_target,
                    ),
                )
                next_tick += chunk_dt
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
        finally:
            await producer_fut

        if self.speaker_play and hasattr(self.speaker_play, "end"):
            self.speaker_play.end()
        await self._publish_state("idle")
```

- [ ] **Step 4: Run all pipeline tests**

```bash
uv run pytest packages/agent/tests/test_pipeline.py -v
```

Expected: all PASS, including the TTFS test from Step 1.

- [ ] **Step 5: Commit**

```bash
git add packages/agent/src/lafufu_agent/tts.py packages/agent/src/lafufu_agent/pipeline.py packages/shared/src/lafufu_shared/testing.py packages/agent/tests/test_pipeline.py
git commit -m "perf(tts): stream Piper synthesis — cuts time-to-first-sound by ~1s"
```

---

## Task 7: Persistent Mic Stream

**Goal:** Stop opening/closing the PyAudio input stream for every utterance. Open once on first listen, keep it open, drain stale buffers between utterances. Saves 50-200ms of startup time per listen + avoids the first-buffer-fragmentation that can clip leading speech.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/__main__.py`

This task has no new unit tests — `RealMic` is integration-tested by manually speaking into the Pi mic. The behavior change is observable as faster time-to-listening after each cycle.

- [ ] **Step 1: Refactor `RealMic` to cache the stream**

In `packages/agent/src/lafufu_agent/__main__.py`, replace the `RealMic` class with:

```python
class RealMic:
    """Records from mic until silence using pre-roll + started-flag VAD, then
    transcribes via STT. Holds a single PyAudio stream open across listen_once
    calls — opening/closing was costing ~50-200ms per cycle.
    """

    PRE_ROLL_S = 0.35
    MAX_RECORD_S = 10.0
    MAX_WAIT_S = 30.0
    MIN_VOICED_CHUNKS = 5

    def __init__(
        self,
        stt,
        *,
        rate: int = 44100,
        chunk_ms: int = 40,
        silence_threshold: int = 800,
        silence_tail_s: float = 1.5,
    ):
        self.stt = stt
        self.rate = rate
        self.chunk_ms = chunk_ms
        self.silence_threshold = silence_threshold
        self.silence_tail_s = silence_tail_s

        # Lazily populated on first listen_once — needs PyAudio init.
        self._stream = None
        self._eff_rate: int | None = None
        self._eff_chunk: int | None = None
        self._device_index: int | None = None

    def set_stt(self, stt) -> None:
        self.stt = stt

    def _ensure_stream(self) -> None:
        """Open the input stream once, with format probing. Subsequent calls are no-ops."""
        if self._stream is not None:
            return

        p = get_pyaudio()
        self._device_index = select_input_device(p)
        eff_rate = self.rate
        try:
            if self._device_index is not None and not p.is_format_supported(
                float(self.rate),
                input_device=self._device_index,
                input_channels=1,
                input_format=pyaudio.paInt16,
            ):
                eff_rate = int(p.get_device_info_by_index(self._device_index).get("defaultSampleRate", 16000))
        except (ValueError, OSError):
            if self._device_index is not None:
                eff_rate = int(p.get_device_info_by_index(self._device_index).get("defaultSampleRate", 16000))

        self._eff_rate = eff_rate
        self._eff_chunk = max(1, int(eff_rate * self.chunk_ms / 1000))
        self._stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._eff_rate,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=self._eff_chunk,
        )

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except OSError:
                pass
            self._stream = None

    def listen_once(self) -> str:
        import collections
        import numpy as np

        self._ensure_stream()
        stream = self._stream
        eff_rate = self._eff_rate
        eff_chunk = self._eff_chunk
        chunks_per_s = eff_rate / eff_chunk
        silence_chunks_end = int(self.silence_tail_s * chunks_per_s)
        pre_roll_size = int(self.PRE_ROLL_S * chunks_per_s)
        max_chunks_recording = int(self.MAX_RECORD_S * chunks_per_s)
        max_chunks_waiting = int(self.MAX_WAIT_S * chunks_per_s)

        # Drain anything that piled up while we were synthesizing/speaking.
        # PyAudio's get_read_available counts samples, not chunks.
        try:
            stale = stream.get_read_available()
            while stale >= eff_chunk:
                stream.read(eff_chunk, exception_on_overflow=False)
                stale -= eff_chunk
        except OSError:
            pass

        pre_roll: collections.deque[bytes] = collections.deque(maxlen=pre_roll_size)
        frames: list[bytes] = []
        started = False
        voiced_chunks = 0
        silent_chunks = 0
        waiting_chunks = 0

        while True:
            data = stream.read(eff_chunk, exception_on_overflow=False)
            rms = audio_rms_bytes(data)
            loud = rms >= self.silence_threshold

            if not started:
                pre_roll.append(data)
                if loud:
                    voiced_chunks += 1
                    if voiced_chunks >= self.MIN_VOICED_CHUNKS:
                        started = True
                        frames.extend(pre_roll)
                        pre_roll.clear()
                else:
                    voiced_chunks = 0
                    waiting_chunks += 1
                    if waiting_chunks > max_chunks_waiting:
                        return ""
                continue

            frames.append(data)
            silent_chunks = silent_chunks + 1 if not loud else 0
            if silent_chunks > silence_chunks_end:
                break
            if len(frames) > max_chunks_recording:
                break

        if not started or not frames:
            return ""

        import audioop

        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self.stt.transcribe(audio_np)
```

- [ ] **Step 2: Wire shutdown**

Add to `AgentService.on_shutdown` in `packages/agent/src/lafufu_agent/service.py`:

```python
    async def on_shutdown(self) -> None:
        await self._publish_state("shutdown")
        if self._mic_loop_task:
            self._mic_loop_task.cancel()
        if hasattr(self._mic, "close"):
            try:
                self._mic.close()
            except Exception as e:
                self.log.warning("mic.close.failed error=%s", e)
```

- [ ] **Step 3: Run agent tests**

```bash
uv run pytest packages/agent/tests/ -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent/src/lafufu_agent/__main__.py packages/agent/src/lafufu_agent/service.py
git commit -m "perf(mic): keep PyAudio input stream open across listen_once calls"
```

---

## Task 8: Release cycle_lock During Silence-Wait

**Goal:** Today `_cycle_lock` is held for the whole `run_one_cycle`, which includes up to 30s of silently waiting for speech onset (`MAX_WAIT_S` in `RealMic`). During that time, text intents from the admin UI hang. Split the lock: take it only once speech actually starts.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/__main__.py`
- Modify: `packages/agent/src/lafufu_agent/service.py`
- Modify: `packages/agent/tests/test_service.py`

The approach: split `RealMic.listen_once()` into two phases — `wait_for_onset()` returns a bool + accumulated pre-roll, and `record_until_silence(pre_roll)` does the rest. The mic loop calls `wait_for_onset()` without the lock; when it returns true, it acquires the lock and finishes recording.

- [ ] **Step 1: Write failing test**

Append to `packages/agent/tests/test_service.py`:

```python
async def test_text_intent_processes_while_mic_is_waiting_for_onset(nats_server):
    """Text intent should NOT wait for the mic to give up on silence.

    Before this change, the mic loop holds _cycle_lock for up to 30s of silence
    listening, blocking text intents. After: mic only takes the lock once it
    detects speech onset.
    """
    class _SilentMic:
        """Pretends to wait for speech onset but always returns empty after a slow pause."""
        def __init__(self):
            self.set_stt_calls = 0
        def wait_for_onset(self):
            import time as _t
            _t.sleep(2.0)  # simulate 2s of silence-listening
            return False, []
        def record_until_silence(self, pre_roll):
            return ""
        def listen_once(self):
            self.wait_for_onset()
            return ""

    svc = AgentService(
        mic=_SilentMic(),
        ollama=FakeOllama(scripts=[("ping", "[neutral]\npong")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.start_mic_loop()
    await asyncio.sleep(0.3)  # let mic loop enter wait_for_onset

    nc = await nats.connect(nats_server)
    replies: list[schemas.AgentReply] = []
    async def cb(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))
    await nc.subscribe(topics.AGENT_REPLY, cb=cb)

    import time as _t
    t0 = _t.monotonic()
    await publish_model(
        nc, topics.AGENT_INTENT_TEXT_MESSAGE, schemas.AgentIntentTextMessage(text="ping")
    )
    # Wait for the reply
    for _ in range(50):
        await asyncio.sleep(0.1)
        if replies:
            break
    elapsed = _t.monotonic() - t0
    await nc.drain()

    assert len(replies) == 1, "text intent should still be processed"
    assert elapsed < 1.0, f"text intent took {elapsed:.2f}s — mic loop is blocking the lock"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest packages/agent/tests/test_service.py::test_text_intent_processes_while_mic_is_waiting_for_onset -v
```

Expected: FAIL — `elapsed` will be ~2s because the mic loop holds the lock.

- [ ] **Step 3: Split RealMic into onset + record phases**

In `packages/agent/src/lafufu_agent/__main__.py`, refactor `RealMic.listen_once` into three methods:

```python
    def wait_for_onset(self) -> tuple[bool, list[bytes]]:
        """Listen until speech starts or MAX_WAIT_S elapses.

        Returns (started, pre_roll_frames). Does NOT hold any external lock —
        safe to run while other coroutines need the agent.
        """
        import collections

        self._ensure_stream()
        stream = self._stream
        eff_rate = self._eff_rate
        eff_chunk = self._eff_chunk
        chunks_per_s = eff_rate / eff_chunk
        pre_roll_size = int(self.PRE_ROLL_S * chunks_per_s)
        max_chunks_waiting = int(self.MAX_WAIT_S * chunks_per_s)

        try:
            stale = stream.get_read_available()
            while stale >= eff_chunk:
                stream.read(eff_chunk, exception_on_overflow=False)
                stale -= eff_chunk
        except OSError:
            pass

        pre_roll: collections.deque[bytes] = collections.deque(maxlen=pre_roll_size)
        voiced_chunks = 0
        waiting_chunks = 0

        while True:
            data = stream.read(eff_chunk, exception_on_overflow=False)
            rms = audio_rms_bytes(data)
            loud = rms >= self.silence_threshold
            pre_roll.append(data)
            if loud:
                voiced_chunks += 1
                if voiced_chunks >= self.MIN_VOICED_CHUNKS:
                    return True, list(pre_roll)
            else:
                voiced_chunks = 0
                waiting_chunks += 1
                if waiting_chunks > max_chunks_waiting:
                    return False, []

    def record_until_silence(self, pre_roll: list[bytes]) -> str:
        """Continue reading after onset, transcribe when silence ends."""
        import audioop
        import numpy as np

        stream = self._stream
        eff_rate = self._eff_rate
        eff_chunk = self._eff_chunk
        chunks_per_s = eff_rate / eff_chunk
        silence_chunks_end = int(self.silence_tail_s * chunks_per_s)
        max_chunks_recording = int(self.MAX_RECORD_S * chunks_per_s)

        frames: list[bytes] = list(pre_roll)
        silent_chunks = 0

        while True:
            data = stream.read(eff_chunk, exception_on_overflow=False)
            rms = audio_rms_bytes(data)
            loud = rms >= self.silence_threshold
            frames.append(data)
            silent_chunks = silent_chunks + 1 if not loud else 0
            if silent_chunks > silence_chunks_end:
                break
            if len(frames) > max_chunks_recording:
                break

        if not frames:
            return ""

        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self.stt.transcribe(audio_np)

    def listen_once(self) -> str:
        """Backward-compat single-call interface (used by text intent paths)."""
        started, pre_roll = self.wait_for_onset()
        if not started:
            return ""
        return self.record_until_silence(pre_roll)
```

- [ ] **Step 4: Modify the mic loop to use split phases**

In `packages/agent/src/lafufu_agent/service.py`, replace `_mic_loop` and add a new `_voice_cycle_with_split_lock`:

```python
    async def _mic_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self._voice_cycle_with_split_lock()
            except Exception as e:
                self.log.exception("voice_cycle.failed error=%s", e)
                await asyncio.sleep(1.0)

    async def _voice_cycle_with_split_lock(self) -> None:
        """Wait for onset WITHOUT holding the lock. Once speech starts, grab the
        lock and finish the cycle. This lets text intents jump in during silence.
        """
        if self._pipeline is None:
            await asyncio.sleep(0.5)
            return

        # Fast-path: if the mic doesn't expose the split interface, just do the
        # old thing (used by tests with FakeMic).
        if not hasattr(self._mic, "wait_for_onset"):
            async with self._cycle_lock:
                await self._pipeline.run_one_cycle()
            return

        loop = asyncio.get_running_loop()
        await self._publish_state("listening")
        started, pre_roll = await loop.run_in_executor(None, self._mic.wait_for_onset)
        if not started:
            await self._publish_state("idle")
            return

        async with self._cycle_lock:
            transcript = await loop.run_in_executor(
                None, self._mic.record_until_silence, pre_roll
            )
            clean = (transcript or "").strip()
            if len(clean) < 2:
                await self._publish_state("idle")
                return
            # Reuse the rest of the pipeline (publish + LLM + speak) by
            # constructing a one-shot mic that returns this transcript.
            class _OnceMic:
                def listen_once(self):
                    return clean
            tmp = VoicePipeline(
                self.nats, _OnceMic(), self._ollama, self._piper, self._speaker_play
            )
            await tmp.run_one_cycle()
```

Add the missing import at the top of `service.py` if not present:

```python
from .pipeline import VoicePipeline
```

- [ ] **Step 5: Run to verify pass**

```bash
uv run pytest packages/agent/tests/test_service.py -v
```

Expected: all PASS, including the new test.

- [ ] **Step 6: Commit**

```bash
git add packages/agent/src/lafufu_agent/__main__.py packages/agent/src/lafufu_agent/service.py packages/agent/tests/test_service.py
git commit -m "fix(agent): release cycle_lock during silence-wait so text intents don't hang"
```

---

## Final Verification on the Pi

After all tasks land and merge, deploy to the Pi and validate end-to-end:

- [ ] **Step 1: Deploy**

```bash
ssh lafufu@lafufu '
  cd /srv/lafufu &&
  git pull --ff-only &&
  sudo ./deploy/install.sh --update &&
  sudo systemctl restart lafufu-agent.service
'
```

- [ ] **Step 2: Verify backend selector populates**

```bash
curl -s http://lafufu:8080/api/agent/stt_backends | jq
```

Expected: list with both backends, `available: true` for openai-whisper, `available: true` for faster-whisper (assuming pre-flight passed).

- [ ] **Step 3: Switch backend live via admin**

Visit `http://lafufu:8080/admin`, audio tab. The new `agent.stt_backend` row should be a dropdown. Switch to `faster-whisper`, save. Check journalctl:

```bash
ssh lafufu@lafufu 'sudo journalctl -u lafufu-agent --since "1 min ago" | grep -E "stt.rebuilt|stt.warmed_up"'
```

Expected: `stt.rebuilt reason=backend backend=faster-whisper` and a follow-up `stt.warmed_up`.

- [ ] **Step 4: Speak into the mic, observe TTFS**

Trigger a reply via mic or `POST /api/agent/text_message {"text": "tell me a joke"}`. Watch the timeline in the admin pulse view. Time-to-first-sound should be visibly faster than before — by maybe a full second on a 4-second reply.

- [ ] **Step 5: Stress-test text intent during silence**

With auto-listen on but the room quiet:

```bash
ssh lafufu@lafufu 'curl -s -X POST -H "Content-Type: application/json" -d "{\"text\":\"hi\"}" http://localhost:8080/api/agent/text_message'
```

The reply should arrive in under a second. Before Task 8, it could take up to 30s.

---

## Self-Review

**Spec coverage check** — the original review identified these issues; each maps to a task:

| Review item | Task |
|---|---|
| Stream Piper synth | Task 6 |
| Run Piper in executor | Task 5 |
| Warm Whisper at startup | Task 2 |
| Skip WAV round-trip | Task 3 |
| Pass real sample rate to aplay | Task 4 |
| Hold input stream open | Task 7 |
| Release cycle_lock during silence | Task 8 |
| faster-whisper backend selector | Task 1 |

No gaps.

**Placeholder scan** — no "TODO", "implement later", or unspecified test code. All code blocks are complete.

**Type consistency** —
- `SttProtocol.transcribe(audio: np.ndarray)` — Task 1 declares this, then Task 1.5 widens it to accept Path too (transitional), then Task 3 leaves it accepting both. Consistent.
- `Piper.synthesize_stream` — added in Task 6.1, consumed in Task 6.2. Consistent.
- `RealMic.wait_for_onset()` + `record_until_silence(pre_roll)` — added in Task 8, consumed in the same task. Consistent.
- `_AplayPlayer(sample_rate=...)` — added in Task 4, consumed in same task. Consistent.

Plan ready.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-19-audio-io-optimizations.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
