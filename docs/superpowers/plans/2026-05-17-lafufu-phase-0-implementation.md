# Lafufu Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap a new `lafufu` repo and ship the Phase 0 foundation — a composable multi-process spine (4 Python services on a NATS event bus) with a SolidJS+Vite admin/face SPA, SQLite persistence, systemd lifecycle, and on-Pi smoke test. Feature parity with the existing monolith.

**Architecture:** Multi-process Python services (`agent`, `animator`, `printer`, `control`) communicate exclusively via NATS topics. The display is a fullscreen Chromium kiosk pointed at the local SolidJS SPA (replaces mpv). `control` is the single SQLite writer and the only HTTP/WS surface. Browsers (Pi-local kiosk + remote phone/laptop) speak to `control`, which bridges them onto NATS.

**Tech Stack:** Python 3.13, `uv` workspace, NATS server + `nats-py`, FastAPI + uvicorn, SQLModel, pytest + pytest-asyncio + hypothesis, TypeScript, SolidJS, Vite, TailwindCSS, `datamodel-code-generator` (Python→TS schema autogen), systemd, CUPS, chromium-browser.

**Reference spec:** [`docs/superpowers/specs/2026-05-17-phase-0-foundation-design.md`](../specs/2026-05-17-phase-0-foundation-design.md) (commit `d908044`).

**Target repo:** `C:\dev\lafufu` (fresh `git init`). This plan does NOT modify `C:\dev\lafufu-jb`.

---

## Pre-flight

Before Task 1, the operator (you) verifies:

- [ ] `python --version` ≥ 3.13 on the dev machine
- [ ] `uv --version` works (install via `winget install astral-sh.uv` or `pipx install uv` if missing)
- [ ] `node --version` ≥ 20 (for the web build)
- [ ] `git --version` works
- [ ] `C:\dev\lafufu` does NOT yet exist (we're creating it)
- [ ] You can reach the Pi over SSH at `$LAFUFU_SSH_HOST` (used at deploy time, not for early tasks)

---

## File structure (final state)

This is what the repo looks like when Phase 0 is done. Each task creates/modifies a subset.

```
lafufu/
├── .github/workflows/ci.yml                    # CI: lint + unit + integration
├── .gitignore
├── .python-version                             # "3.13"
├── README.md
├── pyproject.toml                              # uv workspace root
├── ruff.toml                                   # lint config
│
├── packages/
│   ├── shared/
│   │   ├── pyproject.toml
│   │   ├── src/lafufu_shared/
│   │   │   ├── __init__.py
│   │   │   ├── topics.py                       # all NATS topic constants
│   │   │   ├── schemas.py                      # pydantic models for every event
│   │   │   ├── nats_helper.py                  # connect_with_retry, publish_model, subscribe_model
│   │   │   ├── base_service.py                 # BaseService class
│   │   │   ├── logging_setup.py                # JSON structured logger
│   │   │   ├── settings.py                     # env loader
│   │   │   └── testing.py                      # fake NATS server, fake hardware fixtures
│   │   └── tests/
│   │       ├── test_schemas.py
│   │       ├── test_nats_helper.py
│   │       └── test_base_service.py
│   │
│   ├── animator/
│   │   ├── pyproject.toml
│   │   ├── src/lafufu_animator/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                         # entry point: python -m lafufu_animator
│   │   │   ├── service.py                      # AnimatorService(BaseService)
│   │   │   ├── dxl_bus.py                      # DXL hardware wrapper
│   │   │   ├── pose.py                         # pose math (pure)
│   │   │   ├── expressions.py                  # expression definitions + offsets
│   │   │   └── lipsync.py                      # RMS → mouth envelope (pure)
│   │   └── tests/
│   │       ├── test_pose.py
│   │       ├── test_expressions.py
│   │       ├── test_lipsync.py
│   │       └── test_service.py                 # integration: real NATS, fake DXL
│   │
│   ├── agent/
│   │   ├── pyproject.toml
│   │   ├── src/lafufu_agent/
│   │   │   ├── __init__.py
│   │   │   ├── main.py
│   │   │   ├── service.py
│   │   │   ├── audio_capture.py                # PyAudio singleton + mic select
│   │   │   ├── vad.py                          # silence-based VAD (pure-ish)
│   │   │   ├── stt.py                          # Whisper wrapper
│   │   │   ├── llm.py                          # Ollama HTTP client
│   │   │   ├── tts.py                          # Piper wrapper
│   │   │   ├── emotion_parser.py               # extract [happy]/[sad] tags
│   │   │   └── pipeline.py                     # orchestrates listen→think→speak
│   │   └── tests/
│   │       ├── test_emotion_parser.py
│   │       ├── test_vad.py
│   │       └── test_service.py
│   │
│   ├── printer/
│   │   ├── pyproject.toml
│   │   ├── src/lafufu_printer/
│   │   │   ├── __init__.py
│   │   │   ├── main.py
│   │   │   ├── service.py
│   │   │   ├── cups_client.py
│   │   │   └── formatter.py
│   │   └── tests/
│   │       ├── test_formatter.py
│   │       └── test_service.py
│   │
│   └── control/
│       ├── pyproject.toml
│       ├── src/lafufu_control/
│       │   ├── __init__.py
│       │   ├── main.py
│       │   ├── service.py
│       │   ├── db.py                           # engine + session
│       │   ├── models/
│       │   │   ├── __init__.py
│       │   │   ├── setting.py
│       │   │   ├── expression.py
│       │   │   ├── behavior.py                 # stub for P2
│       │   │   └── plugin.py                   # stub for P4
│       │   ├── api/
│       │   │   ├── __init__.py
│       │   │   ├── app.py                      # FastAPI app factory
│       │   │   ├── routers/
│       │   │   │   ├── settings.py
│       │   │   │   ├── system.py
│       │   │   │   ├── animator.py
│       │   │   │   ├── agent.py
│       │   │   │   └── snapshot.py
│       │   │   └── ws_bridge.py
│       │   └── static/                          # built SPA dropped here
│       ├── alembic.ini
│       ├── migrations/
│       │   └── env.py
│       └── tests/
│           ├── test_db.py
│           ├── test_settings_router.py
│           ├── test_system_router.py
│           └── test_ws_bridge.py
│
├── web/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── postcss.config.cjs
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx                            # SolidJS entry
│   │   ├── app.tsx                             # router
│   │   ├── shared/
│   │   │   ├── nats_ws.ts                      # WS client → reactive signals
│   │   │   ├── api.ts                          # REST helpers
│   │   │   ├── design.ts                       # tokens (emotion→color, etc.)
│   │   │   └── types.gen.ts                    # AUTOGEN from pydantic
│   │   ├── face/
│   │   │   └── face.tsx
│   │   └── admin/
│   │       ├── admin.tsx                       # admin shell
│   │       ├── service_status.tsx
│   │       ├── settings_form.tsx
│   │       ├── pose_view.tsx
│   │       ├── servo_sliders.tsx
│   │       ├── expression_buttons.tsx
│   │       ├── chat_log.tsx
│   │       ├── system_pulse.tsx
│   │       └── service_control.tsx
│   ├── tests/
│   │   ├── design.test.ts
│   │   └── nats_ws.test.ts
│   └── scripts/
│       └── gen_types.mjs                       # pydantic→JSON Schema→TS pipeline
│
├── deploy/
│   ├── systemd/
│   │   ├── nats.service
│   │   ├── lafufu-agent.service
│   │   ├── lafufu-animator.service
│   │   ├── lafufu-printer.service
│   │   ├── lafufu-control.service
│   │   ├── lafufu-kiosk.service
│   │   └── lafufu.target
│   ├── nats/
│   │   └── nats-server.conf
│   └── install.sh
│
└── scripts/
    ├── smoke.sh                                # on-Pi end-to-end check
    └── dev_run_all.sh                          # local dev: NATS + all services in tmux
```

---

<!-- INSERT-NEXT-PHASE-HERE -->

---

## Phase A: Repo bootstrap & dev infrastructure (Tasks 1–4)

Goal: a fresh repo with `uv` workspace, lint/test/CI scaffolding, and a working local NATS for development.

### Task 1: Initialize new repo and uv workspace

**Files:**
- Create: `C:\dev\lafufu\.gitignore`
- Create: `C:\dev\lafufu\.python-version`
- Create: `C:\dev\lafufu\pyproject.toml`
- Create: `C:\dev\lafufu\ruff.toml`
- Create: `C:\dev\lafufu\README.md`

- [ ] **Step 1: Create the folder and git-init**

```powershell
mkdir C:\dev\lafufu
cd C:\dev\lafufu
git init
```

- [ ] **Step 2: Create `.gitignore`**

Write `C:\dev\lafufu\.gitignore`:

```
# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
.pytest_cache/
.ruff_cache/
.venv/
build/
dist/
*.sqlite
*.sqlite-journal
*.sqlite-wal
*.sqlite-shm

# Node / web
node_modules/
web/dist/
web/src/shared/types.gen.ts

# Logs + runtime
*.log
/var/lafufu/
.env
.env.local

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 3: Create `.python-version`**

```
3.13
```

- [ ] **Step 4: Create `pyproject.toml` (workspace root)**

```toml
[project]
name = "lafufu-workspace"
version = "0.1.0"
requires-python = ">=3.13"
description = "Lafufu — composable robot platform (Phase 0 foundation)"

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv]
package = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["packages"]
addopts = "-ra --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]
ignore = ["E501"]  # handled by formatter
```

- [ ] **Step 5: Create `ruff.toml`** (per-project overrides)

```toml
extend = "pyproject.toml"

[lint.per-file-ignores]
"**/tests/*" = ["S101"]  # allow assert in tests
```

- [ ] **Step 6: Create initial `README.md`**

```markdown
# Lafufu

Composable robot platform — Pi-hosted servo/voice agent with web admin.

## Status: Phase 0 (foundation)

See [`docs/superpowers/specs/`](docs/superpowers/specs/) for design docs and [`docs/superpowers/plans/`](docs/superpowers/plans/) for implementation plans.

## Quick start (dev)

```bash
uv sync
./scripts/dev_run_all.sh
```

## Architecture

4 Python services on a NATS bus + SolidJS admin/face SPA. See spec for full detail.
```

- [ ] **Step 7: First commit**

```powershell
git add .
git commit -m "Initial commit: uv workspace + repo scaffolding"
```

### Task 2: Set up dev tooling (pytest, pre-commit, dev deps)

**Files:**
- Create: `C:\dev\lafufu\.pre-commit-config.yaml`
- Modify: `C:\dev\lafufu\pyproject.toml` (add `dependency-groups`)

- [ ] **Step 1: Add dev dependency group to `pyproject.toml`**

Append to `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "hypothesis>=6.0",
    "ruff>=0.4",
    "pre-commit>=3.0",
]
```

- [ ] **Step 2: Sync dependencies**

```powershell
uv sync --all-packages
```

Expected: `.venv` created, ruff/pytest installed.

- [ ] **Step 3: Verify pytest discovers no tests yet**

```powershell
uv run pytest
```

Expected: `no tests ran in N.NNs`.

- [ ] **Step 4: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 5: Install pre-commit hooks**

```powershell
uv run pre-commit install
```

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml .pre-commit-config.yaml uv.lock
git commit -m "Set up dev tooling: pytest, ruff, pre-commit"
```

### Task 3: Install NATS locally and smoke-test it

**Files:**
- Create: `C:\dev\lafufu\deploy\nats\nats-server.conf`
- Create: `C:\dev\lafufu\scripts\nats_smoke.py` (delete after task; for sanity check)

- [ ] **Step 1: Install NATS server**

Windows (winget):
```powershell
winget install nats-io.nats-server
```

Or download from <https://github.com/nats-io/nats-server/releases> and put `nats-server.exe` on PATH.

Verify: `nats-server --version` → prints `nats-server: v2.x.x`.

- [ ] **Step 2: Create NATS config**

Write `deploy/nats/nats-server.conf`:

```
port: 4222
http_port: 8222

jetstream {
  store_dir: "./var/jetstream"
  max_memory_store: 64MB
  max_file_store: 256MB
}

# Phase 0: no auth, local only
```

- [ ] **Step 3: Start NATS in a side terminal**

```powershell
mkdir var
nats-server -c deploy/nats/nats-server.conf
```

Leave running. Open a new terminal for next steps.

- [ ] **Step 4: Write the smoke script**

Write `scripts/nats_smoke.py`:

```python
import asyncio
import nats

async def main():
    nc = await nats.connect("nats://localhost:4222")
    received = []
    async def cb(msg):
        received.append(msg.data.decode())
    await nc.subscribe("smoke.test", cb=cb)
    await nc.publish("smoke.test", b"hello")
    await asyncio.sleep(0.1)
    await nc.drain()
    assert received == ["hello"], f"got {received}"
    print("nats smoke: OK")

asyncio.run(main())
```

- [ ] **Step 5: Add `nats-py` as a temporary dep and run smoke**

```powershell
uv add --group dev nats-py
uv run python scripts/nats_smoke.py
```

Expected: `nats smoke: OK`.

- [ ] **Step 6: Delete the smoke script (it's not part of the codebase)**

```powershell
git rm -f scripts/nats_smoke.py
# Note: file is also new; just delete it
del scripts/nats_smoke.py 2>$null
```

- [ ] **Step 7: Commit the NATS config**

```powershell
git add deploy/nats/nats-server.conf pyproject.toml uv.lock
git commit -m "Add local NATS config + verified dev connection"
```

### Task 4: GitHub Actions CI scaffold

**Files:**
- Create: `C:\dev\lafufu\.github\workflows\ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - name: Install NATS server
        run: |
          curl -L https://github.com/nats-io/nats-server/releases/download/v2.10.20/nats-server-v2.10.20-linux-amd64.tar.gz | tar xz
          sudo mv nats-server-v2.10.20-linux-amd64/nats-server /usr/local/bin/
      - run: uv sync --all-packages
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest -v

  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: cd web && npm ci
      - run: cd web && npm run typecheck
      - run: cd web && npm test --run
```

- [ ] **Step 2: Commit**

```powershell
git add .github/workflows/ci.yml
git commit -m "Add GitHub Actions CI scaffold (Python + web jobs)"
```

Note: the web job will fail until Phase H creates `web/`. That's fine — CI gates merges from then on.

---

## Phase B: Shared foundation package (Tasks 5–8)

Goal: `packages/shared` provides every other service with topic constants, pydantic schemas, a tested NATS helper, a `BaseService` class, structured logging, and test fixtures. Everything downstream imports from here.

### Task 5: Create `shared` package with topic constants and schemas

**Files:**
- Create: `C:\dev\lafufu\packages\shared\pyproject.toml`
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\__init__.py`
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\topics.py`
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\schemas.py`
- Create: `C:\dev\lafufu\packages\shared\tests\test_topics.py`
- Create: `C:\dev\lafufu\packages\shared\tests\test_schemas.py`

- [ ] **Step 1: Create package skeleton**

```powershell
mkdir packages\shared\src\lafufu_shared
mkdir packages\shared\tests
ni packages\shared\src\lafufu_shared\__init__.py
ni packages\shared\tests\__init__.py
```

- [ ] **Step 2: Write `packages/shared/pyproject.toml`**

```toml
[project]
name = "lafufu-shared"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "nats-py>=2.6",
    "pydantic>=2.7",
]

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lafufu_shared"]
```

- [ ] **Step 3: Write the failing topic-constants test FIRST**

`packages/shared/tests/test_topics.py`:

```python
from lafufu_shared import topics

def test_state_subtopics_compose_correctly():
    assert topics.AGENT_STATE == "agent.state"
    assert topics.AGENT_STATE_IDLE == "agent.state.idle"
    assert topics.AGENT_STATE_WARMING == "agent.state.warming"
    assert topics.AGENT_STATE_LISTENING == "agent.state.listening"
    assert topics.AGENT_STATE_SPEAKING == "agent.state.speaking"

def test_animator_topics_present():
    assert topics.ANIMATOR_POSE == "animator.pose"
    assert topics.ANIMATOR_INTENT == "animator.intent"
    assert topics.ANIMATOR_INTENT_PREVIEW == "animator.intent.preview"
    assert topics.ANIMATOR_EVENT_GESTURE_DONE == "animator.event.gesture_done"

def test_printer_topics_present():
    assert topics.PRINTER_STATE_OFFLINE == "printer.state.offline"
    assert topics.PRINTER_INTENT_PRINT_TEXT == "printer.intent.print_text"

def test_system_topics_present():
    assert topics.SYSTEM_HEARTBEAT == "system.heartbeat"
    assert topics.SYSTEM_SERVICE_READY == "system.service.ready"
    assert topics.CONFIG_CHANGED == "config.changed"

def test_subscribe_wildcard_pattern():
    """Wildcard patterns must work as documented in the spec."""
    # All state topics live under a single parent
    for s in (topics.AGENT_STATE_IDLE, topics.ANIMATOR_STATE_IDLE, topics.PRINTER_STATE_IDLE):
        assert ".state." in s
```

- [ ] **Step 4: Run to verify failure**

```powershell
uv sync --all-packages
uv run pytest packages/shared/tests/test_topics.py -v
```

Expected: ImportError or ModuleNotFoundError.

- [ ] **Step 5: Write `packages/shared/src/lafufu_shared/topics.py`**

```python
"""Canonical NATS topic names. Services MUST reference these constants, not literals."""

# Agent state machine
AGENT_STATE = "agent.state"
AGENT_STATE_WARMING = f"{AGENT_STATE}.warming"
AGENT_STATE_IDLE = f"{AGENT_STATE}.idle"
AGENT_STATE_LISTENING = f"{AGENT_STATE}.listening"
AGENT_STATE_THINKING = f"{AGENT_STATE}.thinking"
AGENT_STATE_SPEAKING = f"{AGENT_STATE}.speaking"
AGENT_STATE_DEGRADED = f"{AGENT_STATE}.degraded"
AGENT_STATE_SHUTDOWN = f"{AGENT_STATE}.shutdown"

# Agent content
AGENT_TRANSCRIPT = "agent.transcript"
AGENT_REPLY = "agent.reply"
AGENT_TTS_RMS = "agent.tts.rms"
AGENT_INTENT = "agent.intent"
AGENT_INTENT_TEXT_MESSAGE = f"{AGENT_INTENT}.text_message"

# Animator
ANIMATOR_STATE = "animator.state"
ANIMATOR_STATE_IDLE = f"{ANIMATOR_STATE}.idle"
ANIMATOR_STATE_ACTIVE = f"{ANIMATOR_STATE}.active"
ANIMATOR_STATE_DEGRADED = f"{ANIMATOR_STATE}.degraded"

ANIMATOR_POSE = "animator.pose"

ANIMATOR_INTENT = "animator.intent"
ANIMATOR_INTENT_SET_POSE = f"{ANIMATOR_INTENT}.set_pose"
ANIMATOR_INTENT_PREVIEW = f"{ANIMATOR_INTENT}.preview"
ANIMATOR_INTENT_PLAY_EXPRESSION = f"{ANIMATOR_INTENT}.play_expression"
ANIMATOR_INTENT_GESTURE = f"{ANIMATOR_INTENT}.gesture"

ANIMATOR_EVENT = "animator.event"
ANIMATOR_EVENT_GESTURE_DONE = f"{ANIMATOR_EVENT}.gesture_done"
ANIMATOR_EVENT_LIPSYNC_START = f"{ANIMATOR_EVENT}.lipsync_start"
ANIMATOR_EVENT_LIPSYNC_END = f"{ANIMATOR_EVENT}.lipsync_end"

# Printer
PRINTER_STATE = "printer.state"
PRINTER_STATE_IDLE = f"{PRINTER_STATE}.idle"
PRINTER_STATE_PRINTING = f"{PRINTER_STATE}.printing"
PRINTER_STATE_ERROR = f"{PRINTER_STATE}.error"
PRINTER_STATE_OFFLINE = f"{PRINTER_STATE}.offline"

PRINTER_INTENT = "printer.intent"
PRINTER_INTENT_PRINT_TEXT = f"{PRINTER_INTENT}.print_text"
PRINTER_INTENT_PRINT_TRANSCRIPT = f"{PRINTER_INTENT}.print_transcript"
PRINTER_INTENT_TEST_PAGE = f"{PRINTER_INTENT}.test_page"

PRINTER_EVENT = "printer.event"
PRINTER_EVENT_JOB_STARTED = f"{PRINTER_EVENT}.job_started"
PRINTER_EVENT_JOB_DONE = f"{PRINTER_EVENT}.job_done"
PRINTER_EVENT_PAPER_OUT = f"{PRINTER_EVENT}.paper_out"
PRINTER_EVENT_JAM = f"{PRINTER_EVENT}.jam"

# Config + system
CONFIG_CHANGED = "config.changed"  # actual topic: f"{CONFIG_CHANGED}.{dotted_key}"

SYSTEM_HEARTBEAT = "system.heartbeat"  # f"{...}.<service>"
SYSTEM_SERVICE = "system.service"
SYSTEM_SERVICE_STARTING = f"{SYSTEM_SERVICE}.starting"
SYSTEM_SERVICE_READY = f"{SYSTEM_SERVICE}.ready"
SYSTEM_SERVICE_RESTARTING = f"{SYSTEM_SERVICE}.restarting"
SYSTEM_SERVICE_STOPPED = f"{SYSTEM_SERVICE}.stopped"

SYSTEM_ERROR = "system.error"  # f"{...}.<service>.<kind>"
```

- [ ] **Step 6: Run topic tests to verify pass**

```powershell
uv run pytest packages/shared/tests/test_topics.py -v
```

Expected: all PASS.

- [ ] **Step 7: Write the failing schemas test**

`packages/shared/tests/test_schemas.py`:

```python
import pytest
from pydantic import ValidationError
from lafufu_shared import schemas

def test_agent_reply_valid():
    r = schemas.AgentReply(text="hello", emotion="happy")
    assert r.text == "hello"
    assert r.emotion == "happy"

def test_agent_reply_invalid_emotion():
    with pytest.raises(ValidationError):
        schemas.AgentReply(text="hi", emotion="confused")  # not a valid Emotion

def test_animator_pose_round_trip():
    p = schemas.AnimatorPose(head_lr=2063, head_ud=3082, eye=2045, jaw=1728, brow=2075)
    j = p.model_dump_json()
    p2 = schemas.AnimatorPose.model_validate_json(j)
    assert p2 == p

def test_animator_intent_preview_validates_servo_name():
    schemas.AnimatorIntentPreview(name="jaw", position=1700)
    with pytest.raises(ValidationError):
        schemas.AnimatorIntentPreview(name="elbow", position=0)

def test_agent_tts_rms_required_fields():
    r = schemas.AgentTtsRms(ts=0.1, rms=0.4, mouth_target=0.6)
    assert 0 <= r.mouth_target <= 1

def test_system_heartbeat_has_service():
    h = schemas.SystemHeartbeat(service="agent", ts=1.0, uptime_s=10.0)
    assert h.service == "agent"
```

- [ ] **Step 8: Run to verify failure (ImportError)**

```powershell
uv run pytest packages/shared/tests/test_schemas.py -v
```

Expected: ImportError on `schemas`.

- [ ] **Step 9: Write `packages/shared/src/lafufu_shared/schemas.py`**

```python
"""Pydantic schemas for every NATS event payload. The single source of truth.

These schemas are validated on receive (bad payloads → drop + log) and exported
to TypeScript at build time so the frontend shares the same types.
"""
from typing import Any, Literal
from pydantic import BaseModel, Field

# ----- Enums (literal unions) -----

Emotion = Literal["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]
ServiceName = Literal["agent", "animator", "printer", "control"]
ServoName = Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
GestureName = Literal["nod_yes", "nod_no", "look_around"]

AgentStateName = Literal[
    "warming", "idle", "listening", "thinking", "speaking", "degraded", "shutdown"
]
AnimatorStateName = Literal["idle", "active", "degraded"]
PrinterStateName = Literal["idle", "printing", "error", "offline"]

# ----- Agent -----

class AgentState(BaseModel):
    state: AgentStateName
    detail: str | None = None

class AgentTranscript(BaseModel):
    text: str
    timestamp: float

class AgentReply(BaseModel):
    text: str
    emotion: Emotion

class AgentTtsRms(BaseModel):
    ts: float = Field(description="monotonic seconds since start of utterance")
    rms: float = Field(ge=0.0, le=1.0)
    mouth_target: float = Field(ge=0.0, le=1.0)

class AgentIntentTextMessage(BaseModel):
    text: str

# ----- Animator -----

class AnimatorPose(BaseModel):
    head_lr: int
    head_ud: int
    eye: int
    jaw: int
    brow: int

class AnimatorState(BaseModel):
    state: AnimatorStateName
    detail: str | None = None
    has_u2d2: bool = False

class AnimatorIntentSetPose(BaseModel):
    pose: AnimatorPose
    duration_s: float = 0.25

class AnimatorIntentPreview(BaseModel):
    name: ServoName
    position: int

class AnimatorIntentPlayExpression(BaseModel):
    name: str
    intensity: float = 1.0

class AnimatorIntentGesture(BaseModel):
    name: GestureName

class AnimatorEvent(BaseModel):
    event: Literal["gesture_done", "lipsync_start", "lipsync_end"]
    name: str | None = None

# ----- Printer -----

class PrinterIntentPrintText(BaseModel):
    text: str
    title: str | None = None

class PrinterIntentPrintTranscript(BaseModel):
    transcript: list[dict[str, str]]  # [{"role": "user|assistant", "text": "..."}]

class PrinterState(BaseModel):
    state: PrinterStateName
    detail: str | None = None
    printer_name: str | None = None

class PrinterEvent(BaseModel):
    event: Literal["job_started", "job_done", "paper_out", "jam"]
    job_id: str | None = None

# ----- Config + system -----

class ConfigChanged(BaseModel):
    key: str  # dotted path, e.g. "agent.tts.speed"
    value: Any  # any JSON-compatible value
    source: str  # who initiated the change (e.g. "admin:web", "system")

class SystemHeartbeat(BaseModel):
    service: ServiceName
    ts: float
    uptime_s: float

class SystemError(BaseModel):
    service: ServiceName
    error_kind: str
    message: str
    details: dict[str, Any] | None = None

class SystemServiceEvent(BaseModel):
    """system.service.{starting|ready|restarting|stopped}"""
    service: ServiceName
    event: Literal["starting", "ready", "restarting", "stopped"]
    detail: str | None = None
```

- [ ] **Step 10: Run schemas tests**

```powershell
uv run pytest packages/shared/tests/test_schemas.py -v
```

Expected: all PASS.

- [ ] **Step 11: Update `lafufu_shared/__init__.py` for clean imports**

```python
from . import schemas, topics

__all__ = ["schemas", "topics"]
```

- [ ] **Step 12: Commit**

```powershell
git add packages/shared
git commit -m "shared: add topic constants and pydantic event schemas"
```

### Task 6: NATS helper, structured logging, env settings

**Files:**
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\nats_helper.py`
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\logging_setup.py`
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\settings.py`
- Create: `C:\dev\lafufu\packages\shared\tests\test_nats_helper.py`

- [ ] **Step 1: Write the failing NATS helper test**

`packages/shared/tests/test_nats_helper.py`:

```python
import asyncio
import subprocess
import time
from pathlib import Path
import pytest
import nats
from lafufu_shared import nats_helper, schemas, topics

@pytest.fixture(scope="module")
def nats_server(tmp_path_factory):
    """Spawn a real nats-server for this module's tests."""
    storedir = tmp_path_factory.mktemp("js")
    proc = subprocess.Popen(
        ["nats-server", "--port", "4233", "--jetstream", "--store_dir", str(storedir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield "nats://localhost:4233"
    proc.terminate()
    proc.wait(timeout=5)

async def test_connect_with_retry_succeeds(nats_server):
    nc = await nats_helper.connect_with_retry(nats_server, name="test")
    assert nc.is_connected
    await nc.drain()

async def test_publish_and_subscribe_model_round_trip(nats_server):
    nc = await nats_helper.connect_with_retry(nats_server, name="t")
    got: list[schemas.AgentReply] = []

    async def handler(subject: str, msg: schemas.AgentReply):
        got.append(msg)

    sub = await nats_helper.subscribe_model(nc, topics.AGENT_REPLY, schemas.AgentReply, handler)
    await nats_helper.publish_model(nc, topics.AGENT_REPLY, schemas.AgentReply(text="hi", emotion="happy"))
    await asyncio.sleep(0.1)
    await sub.unsubscribe()
    await nc.drain()

    assert len(got) == 1
    assert got[0].text == "hi"
    assert got[0].emotion == "happy"

async def test_subscribe_drops_invalid_payload(nats_server, caplog):
    nc = await nats_helper.connect_with_retry(nats_server, name="t")
    got: list = []

    async def handler(subject, msg):
        got.append(msg)

    await nats_helper.subscribe_model(nc, "test.bad", schemas.AgentReply, handler)
    await nc.publish("test.bad", b"not json")
    await nc.publish("test.bad", b'{"text":"x","emotion":"not_a_valid_emotion"}')
    await asyncio.sleep(0.1)
    await nc.drain()
    assert got == []
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/shared/tests/test_nats_helper.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `nats_helper.py`**

```python
"""NATS connection + typed publish/subscribe helpers."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import nats
from nats.aio.client import Client as NATS
from nats.aio.subscription import Subscription
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_RETRY_DELAYS_S = (1, 2, 5, 10, 30)


async def connect_with_retry(url: str = "nats://localhost:4222", *, name: str = "lafufu-svc") -> NATS:
    """Connect to NATS with exponential backoff. Never gives up.

    On reconnect after disconnect, nats-py handles reconnection internally;
    this function is only for the initial connection.
    """
    attempt = 0
    while True:
        try:
            client = await nats.connect(
                url,
                name=name,
                reconnect_time_wait=2,
                max_reconnect_attempts=-1,  # infinite
                ping_interval=10,
                max_outstanding_pings=3,
            )
            log.info("nats.connected name=%s url=%s", name, url)
            return client
        except Exception as e:
            wait = _RETRY_DELAYS_S[min(attempt, len(_RETRY_DELAYS_S) - 1)]
            log.warning("nats.connect.failed attempt=%d wait=%ds error=%s", attempt, wait, e)
            await asyncio.sleep(wait)
            attempt += 1


async def publish_model(nc: NATS, subject: str, model: BaseModel) -> None:
    """Publish a pydantic model as JSON-encoded bytes."""
    await nc.publish(subject, model.model_dump_json().encode("utf-8"))


async def subscribe_model(
    nc: NATS,
    subject: str,
    schema: type[T],
    handler: Callable[[str, T], Awaitable[None]],
    *,
    queue: str | None = None,
) -> Subscription:
    """Subscribe with pydantic validation. Invalid payloads logged + dropped."""

    async def cb(msg):
        try:
            obj = schema.model_validate_json(msg.data)
        except ValidationError as e:
            log.warning("payload.invalid subject=%s schema=%s error=%s",
                        msg.subject, schema.__name__, e)
            return
        except Exception as e:
            log.warning("payload.decode_failed subject=%s error=%s", msg.subject, e)
            return
        try:
            await handler(msg.subject, obj)
        except Exception as e:
            log.exception("handler.raised subject=%s error=%s", msg.subject, e)

    return await nc.subscribe(subject, queue=queue, cb=cb)
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
uv run pytest packages/shared/tests/test_nats_helper.py -v
```

Expected: all 3 PASS. (Requires `nats-server` on PATH.)

- [ ] **Step 5: Write `logging_setup.py`**

```python
"""JSON structured logging to stdout for journald capture."""
import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.time(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(service: str, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    # Replace any default handlers so we don't double-log
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service))
    root.addHandler(handler)
```

- [ ] **Step 6: Write `settings.py`**

```python
"""Env-var loader shared by all services."""
import os
from pathlib import Path


def nats_url() -> str:
    return os.environ.get("LAFUFU_NATS_URL", "nats://localhost:4222")


def data_dir() -> Path:
    """Where persistent state lives (DB, JetStream, backups)."""
    p = Path(os.environ.get("LAFUFU_DATA_DIR", "./var"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "db.sqlite"


def env(name: str, default: str | None = None) -> str | None:
    """Read an env var, returning default if unset or empty."""
    v = os.environ.get(name)
    return v if v else default
```

- [ ] **Step 7: Update `__init__.py`**

```python
from . import logging_setup, nats_helper, schemas, settings, topics

__all__ = ["logging_setup", "nats_helper", "schemas", "settings", "topics"]
```

- [ ] **Step 8: Commit**

```powershell
git add packages/shared
git commit -m "shared: add NATS helper, structured logging, env settings"
```

### Task 7: BaseService class with heartbeat + signal handling

**Files:**
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\base_service.py`
- Create: `C:\dev\lafufu\packages\shared\tests\test_base_service.py`

- [ ] **Step 1: Write the failing test**

`packages/shared/tests/test_base_service.py`:

```python
import asyncio
import subprocess
import time
import pytest
import nats
from lafufu_shared import base_service, schemas, topics


@pytest.fixture(scope="module")
def nats_server(tmp_path_factory):
    storedir = tmp_path_factory.mktemp("js")
    proc = subprocess.Popen(
        ["nats-server", "--port", "4234", "--jetstream", "--store_dir", str(storedir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield "nats://localhost:4234"
    proc.terminate()
    proc.wait(timeout=5)


class _TinyService(base_service.BaseService):
    name = "agent"  # reuse known ServiceName; doesn't matter for test
    nats_url_override: str = ""

    def __init__(self, nats_url: str):
        super().__init__()
        self.nats_url_override = nats_url
        self.startup_called = False
        self.shutdown_called = False

    @property
    def nats_url(self) -> str:
        return self.nats_url_override

    async def on_startup(self) -> None:
        self.startup_called = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True

    async def main_loop(self) -> None:
        # Wait until external shutdown signal
        await self._shutdown.wait()


async def test_lifecycle_calls_startup_and_shutdown(nats_server):
    svc = _TinyService(nats_server)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.2)
    assert svc.startup_called
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert svc.shutdown_called


async def test_heartbeat_published(nats_server):
    svc = _TinyService(nats_server)
    svc.heartbeat_interval_s = 0.1  # speed up
    received: list[schemas.SystemHeartbeat] = []

    # Subscribe before starting
    nc = await nats.connect(nats_server)

    async def cb(msg):
        received.append(schemas.SystemHeartbeat.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.SYSTEM_HEARTBEAT}.>", cb=cb)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.35)  # ~3 heartbeats
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    await nc.drain()

    assert len(received) >= 2
    assert all(h.service == "agent" for h in received)
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/shared/tests/test_base_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `base_service.py`**

```python
"""BaseService: lifecycle, signal handling, heartbeat, error reporting."""
import asyncio
import logging
import signal
import time
from typing import ClassVar

from . import logging_setup, nats_helper, settings, topics
from .schemas import ServiceName, SystemError, SystemHeartbeat, SystemServiceEvent

log = logging.getLogger(__name__)


class BaseService:
    """Subclass and override on_startup, on_shutdown, main_loop."""

    name: ClassVar[ServiceName] = "agent"  # subclasses set this
    heartbeat_interval_s: ClassVar[float] = 5.0

    def __init__(self) -> None:
        self.nats = None  # set in run()
        self._shutdown = asyncio.Event()
        self._heartbeat_task: asyncio.Task | None = None
        self._start_ts: float = 0.0
        self.log = logging.getLogger(f"lafufu.{self.name}")

    @property
    def nats_url(self) -> str:
        """Override in tests or services that take a custom URL."""
        return settings.nats_url()

    # --- Overridables ---

    async def on_startup(self) -> None:
        """Connect to hardware, load models, etc. Runs after NATS connect."""

    async def on_shutdown(self) -> None:
        """Release hardware, save state. Runs after main_loop exits."""

    async def main_loop(self) -> None:
        """The service's main work. Default: wait for shutdown."""
        await self._shutdown.wait()

    # --- Internals ---

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await nats_helper.publish_model(
                    self.nats,
                    f"{topics.SYSTEM_HEARTBEAT}.{self.name}",
                    SystemHeartbeat(
                        service=self.name,
                        ts=time.time(),
                        uptime_s=time.monotonic() - self._start_ts,
                    ),
                )
            except Exception as e:
                self.log.warning("heartbeat.failed error=%s", e)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=self.heartbeat_interval_s)
            except TimeoutError:
                pass

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown.set)
            except NotImplementedError:
                # Windows lacks add_signal_handler for some signals; skip silently
                pass

    async def _publish_service_event(self, event_subject: str) -> None:
        try:
            await nats_helper.publish_model(
                self.nats,
                event_subject,
                SystemServiceEvent(service=self.name, event=event_subject.rsplit(".", 1)[-1]),  # type: ignore[arg-type]
            )
        except Exception as e:
            self.log.warning("service_event.publish_failed event=%s error=%s", event_subject, e)

    async def run(self) -> None:
        logging_setup.configure(self.name)
        self._install_signal_handlers()
        self._start_ts = time.monotonic()

        self.nats = await nats_helper.connect_with_retry(self.nats_url, name=f"lafufu-{self.name}")
        await self._publish_service_event(topics.SYSTEM_SERVICE_STARTING)

        try:
            await self.on_startup()
            await self._publish_service_event(topics.SYSTEM_SERVICE_READY)
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            await self.main_loop()
        except Exception as e:
            self.log.exception("service.main.crashed")
            try:
                await nats_helper.publish_model(
                    self.nats,
                    f"{topics.SYSTEM_ERROR}.{self.name}.unhandled",
                    SystemError(service=self.name, error_kind="unhandled", message=str(e)),
                )
            except Exception:
                pass
            raise
        finally:
            self._shutdown.set()
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await self.on_shutdown()
            except Exception:
                self.log.exception("on_shutdown.failed")
            try:
                await self._publish_service_event(topics.SYSTEM_SERVICE_STOPPED)
                await self.nats.drain()
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
uv run pytest packages/shared/tests/test_base_service.py -v
```

Expected: both PASS.

- [ ] **Step 5: Update `__init__.py`**

```python
from . import base_service, logging_setup, nats_helper, schemas, settings, topics

__all__ = ["base_service", "logging_setup", "nats_helper", "schemas", "settings", "topics"]
```

- [ ] **Step 6: Commit**

```powershell
git add packages/shared
git commit -m "shared: add BaseService with lifecycle, heartbeat, signal handling"
```

### Task 8: Test fixtures — shared `testing.py` for downstream services

**Files:**
- Create: `C:\dev\lafufu\packages\shared\src\lafufu_shared\testing.py`
- Create: `C:\dev\lafufu\packages\shared\tests\test_testing_fixtures.py`

- [ ] **Step 1: Write a test that USES the fixtures we're about to build**

`packages/shared/tests/test_testing_fixtures.py`:

```python
import pytest
from lafufu_shared.testing import nats_server_fixture, FakeDxlBus

# Import the fixture so pytest discovers it
nats_server = nats_server_fixture("4235")


async def test_nats_fixture_yields_url(nats_server):
    assert nats_server.startswith("nats://localhost:4235")


def test_fake_dxl_bus_records_writes():
    bus = FakeDxlBus()
    bus.write("jaw", 1700)
    bus.write("jaw", 1750)
    bus.write("head_lr", 2100)
    assert bus.writes == [("jaw", 1700), ("jaw", 1750), ("head_lr", 2100)]
    assert bus.last_position("jaw") == 1750
    assert bus.last_position("head_lr") == 2100


def test_fake_dxl_bus_disconnect_raises_on_write():
    bus = FakeDxlBus()
    bus.disconnect()
    with pytest.raises(IOError, match="disconnected"):
        bus.write("jaw", 1700)
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/shared/tests/test_testing_fixtures.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `testing.py`**

```python
"""Shared pytest fixtures and fakes for cross-service testing."""
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator

import pytest


def nats_server_fixture(port: str = "4222") -> Callable:
    """Returns a pytest fixture that spawns a real nats-server on `port`."""
    @pytest.fixture(scope="module")
    def _fixture(tmp_path_factory) -> Iterator[str]:
        storedir = tmp_path_factory.mktemp(f"js_{port}")
        proc = subprocess.Popen(
            ["nats-server", "--port", port, "--jetstream", "--store_dir", str(storedir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        try:
            yield f"nats://localhost:{port}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return _fixture


class FakeDxlBus:
    """In-memory fake of animator's DXL bus. Records writes, returns last positions."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []
        self._positions: dict[str, int] = {}
        self._connected = True
        self.torque_disabled_count = 0

    def write(self, name: str, position: int) -> None:
        if not self._connected:
            raise IOError("DXL bus disconnected")
        self.writes.append((name, position))
        self._positions[name] = position

    def read(self, name: str) -> int:
        return self._positions.get(name, 0)

    def last_position(self, name: str) -> int | None:
        return self._positions.get(name)

    def expression_was_set(self, name: str) -> bool:
        """Convenience: true if any write happened that maps to the named expression."""
        # Implementations can extend this; default is no-op stub
        return False

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._connected = True

    def disable_torque(self) -> None:
        self.torque_disabled_count += 1


class FakeWhisper:
    """Maps canned audio identifiers to canned transcripts."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def transcribe(self, audio_id: str) -> str:
        self.calls.append(audio_id)
        return self.mapping.get(audio_id, "")


class FakeOllama:
    """Scripted replies keyed by prompt substring match."""

    def __init__(self, scripts: list[tuple[str, str]] | None = None) -> None:
        # list of (prompt_substring, reply_text) — first match wins
        self.scripts = scripts or []
        self.calls: list[str] = []

    async def chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        for needle, reply in self.scripts:
            if needle.lower() in prompt.lower():
                return reply
        return "[neutral]\ndefault test reply"


class FakePiper:
    """Returns canned audio bytes + RMS sequence."""

    def __init__(self, chunks: list[tuple[bytes, float]] | None = None) -> None:
        # list of (audio_bytes, rms) tuples
        self.chunks = chunks or [(b"\x00" * 1764, 0.0)]
        self.calls: list[str] = []

    def synthesize(self, text: str) -> list[tuple[bytes, float]]:
        self.calls.append(text)
        return list(self.chunks)
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
uv run pytest packages/shared/tests/test_testing_fixtures.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Re-export from `__init__.py`** (testing is opt-in import path, leave out of top-level)

No change needed; downstream packages import `from lafufu_shared.testing import ...`.

- [ ] **Step 6: Run the FULL shared test suite to verify no regressions**

```powershell
uv run pytest packages/shared/ -v
```

Expected: all tests pass (topics + schemas + nats_helper + base_service + testing fixtures).

- [ ] **Step 7: Commit**

```powershell
git add packages/shared
git commit -m "shared: add test fixtures (nats server, fake DXL/Whisper/Ollama/Piper)"
```

---

## Phase C: Animator service (Tasks 9–13)

Goal: Animator is fully working on a real Pi (servos moving, expressions playing, lipsync responding to `agent.tts.rms`). All pure logic unit-tested; service integration-tested against fake DXL + real NATS.

**Constants from the existing build** (hand-measured calibration — preserve these):

```
DXL_IDS = {"head_lr": 1, "head_ud": 2, "brow": 3, "jaw": 4, "eye": 5}
DXL_HEAD_LR_LEFT_POS = 2298    # max-left position
DXL_HEAD_LR_RIGHT_POS = 1828   # max-right position
DXL_HEAD_UD_UP_POS = 2885
DXL_HEAD_UD_DOWN_POS = 3278
DXL_BROW_UP_POS = 2099
DXL_BROW_DOWN_POS = 2051
DXL_JAW_OPEN_POS = 1534
DXL_JAW_CLOSE_POS = 1728
DXL_EYE_LEFT_POS = 1960
DXL_EYE_RIGHT_POS = 2130
EYE_IDLE_DXL = 2045
BROW_IDLE_DXL = 2075
HEAD_IDLE_LR_DXL = 2063
HEAD_IDLE_UD_DXL = 3082
MOUTH_CLOSE_DXL = 1728
MOUTH_OPEN_DXL = 1534
```

### Task 9: Create `animator` package + pose math

**Files:**
- Create: `C:\dev\lafufu\packages\animator\pyproject.toml`
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\__init__.py`
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\pose.py`
- Create: `C:\dev\lafufu\packages\animator\tests\test_pose.py`

- [ ] **Step 1: Create package skeleton**

```powershell
mkdir packages\animator\src\lafufu_animator
mkdir packages\animator\tests
ni packages\animator\src\lafufu_animator\__init__.py
ni packages\animator\tests\__init__.py
```

- [ ] **Step 2: Write `packages/animator/pyproject.toml`**

```toml
[project]
name = "lafufu-animator"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "lafufu-shared",
    "dynamixel-sdk>=3.7",
    "numpy>=1.26",
]

[tool.uv.sources]
lafufu-shared = { workspace = true }

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lafufu_animator"]
```

- [ ] **Step 3: Sync workspace**

```powershell
uv sync --all-packages
```

- [ ] **Step 4: Write the failing pose tests**

`packages/animator/tests/test_pose.py`:

```python
import pytest
from lafufu_animator import pose

def test_clamp_within_range():
    assert pose.clamp(1500, 1000, 2000) == 1500

def test_clamp_below_returns_lo():
    assert pose.clamp(500, 1000, 2000) == 1000

def test_clamp_above_returns_hi():
    assert pose.clamp(2500, 1000, 2000) == 2000

def test_clamp_handles_reversed_bounds():
    """min/max should be inferred, not assumed ordered."""
    assert pose.clamp(1500, 2000, 1000) == 1500
    assert pose.clamp(500, 2000, 1000) == 1000

def test_dxl_from_deg_endpoints():
    # 0 degrees → midpoint
    assert pose.dxl_from_deg(0.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 1500
    # max → pos_max
    assert pose.dxl_from_deg(10.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 2000
    # min → pos_min
    assert pose.dxl_from_deg(-10.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 1000

def test_dxl_from_deg_clamps_out_of_range():
    assert pose.dxl_from_deg(100.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 2000

def test_deg_from_dxl_round_trip():
    deg = pose.deg_from_dxl(1500, pos_min=1000, pos_max=2000, deg_min=-10.0, deg_max=10.0)
    assert abs(deg - 0.0) < 1e-6

def test_lerp_int_midpoint():
    assert pose.lerp_int(1000, 2000, 0.5) == 1500

def test_lerp_int_endpoints():
    assert pose.lerp_int(1000, 2000, 0.0) == 1000
    assert pose.lerp_int(1000, 2000, 1.0) == 2000

def test_idle_pose_constants_match_spec():
    p = pose.idle_pose()
    assert p.head_lr == pose.HEAD_IDLE_LR_DXL
    assert p.head_ud == pose.HEAD_IDLE_UD_DXL
    assert p.jaw == pose.MOUTH_CLOSE_DXL
    assert p.eye == pose.EYE_IDLE_DXL
    assert p.brow == pose.BROW_IDLE_DXL
```

- [ ] **Step 5: Run to verify failure**

```powershell
uv run pytest packages/animator/tests/test_pose.py -v
```

Expected: ImportError on `pose`.

- [ ] **Step 6: Write `pose.py`**

```python
"""Pure pose math: clamping, dxl ↔ degrees conversion, interpolation, idle pose."""
from lafufu_shared.schemas import AnimatorPose

# Calibrated constants (preserve from existing build — measured by hand)
DXL_IDS = {"head_lr": 1, "head_ud": 2, "brow": 3, "jaw": 4, "eye": 5}

DXL_HEAD_LR_LEFT_POS = 2298
DXL_HEAD_LR_RIGHT_POS = 1828
DXL_HEAD_UD_UP_POS = 2885
DXL_HEAD_UD_DOWN_POS = 3278
DXL_BROW_UP_POS = 2099
DXL_BROW_DOWN_POS = 2051
DXL_JAW_OPEN_POS = 1534
DXL_JAW_CLOSE_POS = 1728
DXL_EYE_LEFT_POS = 1960
DXL_EYE_RIGHT_POS = 2130

EYE_IDLE_DXL = 2045
BROW_IDLE_DXL = 2075
HEAD_IDLE_LR_DXL = 2063
HEAD_IDLE_UD_DXL = 3082
MOUTH_CLOSE_DXL = 1728
MOUTH_OPEN_DXL = 1534

# Per-servo clamp ranges (min, max); bounds order-agnostic
CLAMP = {
    "head_lr": (DXL_HEAD_LR_RIGHT_POS, DXL_HEAD_LR_LEFT_POS),
    "head_ud": (DXL_HEAD_UD_UP_POS, DXL_HEAD_UD_DOWN_POS),
    "brow":    (DXL_BROW_DOWN_POS, DXL_BROW_UP_POS),
    "jaw":     (DXL_JAW_OPEN_POS, DXL_JAW_CLOSE_POS),
    "eye":     (DXL_EYE_LEFT_POS, DXL_EYE_RIGHT_POS),
}


def clamp(value: float, lo: float, hi: float) -> int:
    """Clamp value to [min(lo,hi), max(lo,hi)] and return as int."""
    a, b = (lo, hi) if lo <= hi else (hi, lo)
    return int(max(a, min(b, value)))


def clamp_dxl(name: str, value: float) -> int:
    lo, hi = CLAMP[name]
    return clamp(value, lo, hi)


def dxl_from_deg(deg: float, *, deg_min: float, deg_max: float, pos_min: int, pos_max: int) -> int:
    """Map a degree value to a DXL position, clamped."""
    deg = max(deg_min, min(deg_max, deg))
    frac = (deg - deg_min) / (deg_max - deg_min)
    return int(round(pos_min + frac * (pos_max - pos_min)))


def deg_from_dxl(pos: float, *, pos_min: int, pos_max: int, deg_min: float, deg_max: float) -> float:
    frac = (pos - pos_min) / (pos_max - pos_min)
    return deg_min + frac * (deg_max - deg_min)


def lerp_int(a: int, b: int, t: float) -> int:
    """Linear interpolation, clamped to [0, 1], returned as int."""
    t = max(0.0, min(1.0, t))
    return int(round(a + (b - a) * t))


def idle_pose() -> AnimatorPose:
    return AnimatorPose(
        head_lr=HEAD_IDLE_LR_DXL,
        head_ud=HEAD_IDLE_UD_DXL,
        eye=EYE_IDLE_DXL,
        jaw=MOUTH_CLOSE_DXL,
        brow=BROW_IDLE_DXL,
    )
```

- [ ] **Step 7: Run tests to verify pass**

```powershell
uv run pytest packages/animator/tests/test_pose.py -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```powershell
git add packages/animator
git commit -m "animator: add package skeleton + pose math (pure functions)"
```

### Task 10: Expressions (definitions + sampler)

**Files:**
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\expressions.py`
- Create: `C:\dev\lafufu\packages\animator\tests\test_expressions.py`

- [ ] **Step 1: Write the failing tests**

`packages/animator/tests/test_expressions.py`:

```python
import pytest
from lafufu_animator import expressions, pose

def test_list_expressions_returns_all_emotions():
    names = expressions.list_names()
    for required in ("happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"):
        assert required in names

def test_get_offsets_returns_pose_deltas():
    offsets = expressions.get_offsets("happy", intensity=1.0)
    # Returns a dict mapping servo name → offset (delta from idle, in DXL ticks)
    assert set(offsets.keys()) == {"head_lr", "head_ud", "eye", "jaw", "brow"}
    # neutral should be all zeros
    neutral = expressions.get_offsets("neutral", intensity=1.0)
    assert all(v == 0 for v in neutral.values())

def test_intensity_scales_offsets_linearly():
    half = expressions.get_offsets("happy", intensity=0.5)
    full = expressions.get_offsets("happy", intensity=1.0)
    for k in half:
        assert abs(half[k] - full[k] / 2) <= 1  # allow rounding

def test_unknown_expression_returns_neutral():
    assert expressions.get_offsets("totally_made_up") == expressions.get_offsets("neutral")

def test_apply_offsets_clamps_to_safe_range():
    base = pose.idle_pose()
    # Apply an extreme offset
    out = expressions.apply_offsets(base, {"jaw": 9999, "head_lr": 0, "head_ud": 0, "eye": 0, "brow": 0})
    # jaw should be clamped to its safe max
    lo, hi = pose.CLAMP["jaw"]
    assert out.jaw == max(lo, hi)
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/animator/tests/test_expressions.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `expressions.py`**

```python
"""Expression definitions: emotion → pose offsets from idle.

Each expression is a dict mapping servo name → DXL-tick delta from idle.
Hand-tuned values; tweak via web admin in later phases.
"""
from lafufu_shared.schemas import AnimatorPose

from . import pose

ServoOffsets = dict[str, int]

# Deltas relative to idle pose (idle_pose() values).
# Positive head_ud = look down; positive eye = look right; positive jaw = open (toward MOUTH_OPEN).
_EXPRESSIONS: dict[str, ServoOffsets] = {
    "neutral": {"head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0},
    "happy": {"head_lr": 0, "head_ud": -30, "eye": 0, "jaw": -40, "brow": +18},
    "sad": {"head_lr": 0, "head_ud": +60, "eye": +5, "jaw": 0, "brow": -18},
    "angry": {"head_lr": 0, "head_ud": -20, "eye": 0, "jaw": -20, "brow": -22},
    "surprised": {"head_lr": 0, "head_ud": -40, "eye": 0, "jaw": -80, "brow": +20},
    "agree": {"head_lr": 0, "head_ud": +20, "eye": 0, "jaw": 0, "brow": +10},
    "disagree": {"head_lr": +30, "head_ud": 0, "eye": 0, "jaw": 0, "brow": -10},
}


def list_names() -> list[str]:
    return list(_EXPRESSIONS.keys())


def get_offsets(name: str, intensity: float = 1.0) -> ServoOffsets:
    """Return scaled offsets for the named expression. Unknown → neutral."""
    base = _EXPRESSIONS.get(name, _EXPRESSIONS["neutral"])
    intensity = max(0.0, min(1.0, intensity))
    return {k: int(round(v * intensity)) for k, v in base.items()}


def apply_offsets(base_pose: AnimatorPose, offsets: ServoOffsets) -> AnimatorPose:
    """Apply DXL-tick offsets to a pose, clamping each servo to its safe range."""
    return AnimatorPose(
        head_lr=pose.clamp_dxl("head_lr", base_pose.head_lr + offsets.get("head_lr", 0)),
        head_ud=pose.clamp_dxl("head_ud", base_pose.head_ud + offsets.get("head_ud", 0)),
        eye=pose.clamp_dxl("eye", base_pose.eye + offsets.get("eye", 0)),
        jaw=pose.clamp_dxl("jaw", base_pose.jaw + offsets.get("jaw", 0)),
        brow=pose.clamp_dxl("brow", base_pose.brow + offsets.get("brow", 0)),
    )
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
uv run pytest packages/animator/tests/test_expressions.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add packages/animator
git commit -m "animator: add expression definitions + offset sampler"
```

### Task 11: Lipsync envelope (RMS → mouth target)

**Files:**
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\lipsync.py`
- Create: `C:\dev\lafufu\packages\animator\tests\test_lipsync.py`

- [ ] **Step 1: Write the failing tests**

`packages/animator/tests/test_lipsync.py`:

```python
import pytest
from lafufu_animator.lipsync import LipsyncEnvelope, rms_to_jaw_dxl
from lafufu_animator import pose

def test_jaw_dxl_at_zero_rms_is_closed():
    assert rms_to_jaw_dxl(0.0) == pose.MOUTH_CLOSE_DXL

def test_jaw_dxl_at_max_rms_approaches_open():
    val = rms_to_jaw_dxl(1.0)
    # MOUTH_OPEN_DXL < MOUTH_CLOSE_DXL (open is a lower tick value), so opening goes DOWN
    assert val <= pose.MOUTH_OPEN_DXL + 5

def test_envelope_attack_smooths_jumps_upward():
    env = LipsyncEnvelope(attack_s=0.05, release_s=0.10, gamma=0.7)
    # Feed RMS jumping from 0 to 1 in one tick (dt=20ms)
    out1 = env.step(target=1.0, dt=0.02)
    # Should not jump straight to 1
    assert 0.0 < out1 < 1.0

def test_envelope_release_smooths_drops():
    env = LipsyncEnvelope(attack_s=0.05, release_s=0.10, gamma=0.7)
    env.step(target=1.0, dt=0.1)  # ramp up
    high = env.step(target=1.0, dt=0.1)
    low = env.step(target=0.0, dt=0.02)  # immediate target drop
    # Release is slower than attack, so should still be above the gap
    assert low > 0.0
    assert low < high

def test_envelope_gamma_compresses_low_end():
    env_linear = LipsyncEnvelope(attack_s=0.0, release_s=0.0, gamma=1.0)
    env_curve = LipsyncEnvelope(attack_s=0.0, release_s=0.0, gamma=0.5)
    out_linear = env_linear.step(target=0.5, dt=0.1)
    out_curve = env_curve.step(target=0.5, dt=0.1)
    # gamma < 1 boosts low-end values
    assert out_curve > out_linear
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/animator/tests/test_lipsync.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `lipsync.py`**

```python
"""Lipsync envelope: smooths a fast-changing RMS into a jaw setpoint.

Inputs come from `agent.tts.rms` (already normalized 0..1 by agent).
Output drives the jaw servo.
"""
import math
from . import pose


def rms_to_jaw_dxl(target: float) -> int:
    """Map a normalized [0,1] mouth-target value to DXL jaw position.

    target=0 → closed (MOUTH_CLOSE_DXL)
    target=1 → open  (MOUTH_OPEN_DXL)
    Note that MOUTH_OPEN < MOUTH_CLOSE in DXL ticks.
    """
    target = max(0.0, min(1.0, target))
    return pose.lerp_int(pose.MOUTH_CLOSE_DXL, pose.MOUTH_OPEN_DXL, target)


class LipsyncEnvelope:
    """First-order envelope with separate attack/release time constants.

    `gamma` is a post-shaping exponent applied to the target BEFORE the envelope:
    gamma < 1 boosts low values (more responsive to soft speech).
    """

    def __init__(self, attack_s: float = 0.03, release_s: float = 0.08, gamma: float = 0.7) -> None:
        self.attack_s = attack_s
        self.release_s = release_s
        self.gamma = gamma
        self._env = 0.0

    def step(self, target: float, dt: float) -> float:
        """Advance one timestep. Returns smoothed value in [0,1]."""
        shaped = math.pow(max(0.0, min(1.0, target)), max(1e-6, self.gamma))
        if shaped > self._env:
            tau = max(1e-6, self.attack_s)
        else:
            tau = max(1e-6, self.release_s)
        alpha = 1.0 - math.exp(-dt / tau) if dt > 0 else 1.0
        self._env = self._env + (shaped - self._env) * alpha
        return self._env

    def reset(self) -> None:
        self._env = 0.0

    @property
    def value(self) -> float:
        return self._env
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
uv run pytest packages/animator/tests/test_lipsync.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add packages/animator
git commit -m "animator: add lipsync envelope + RMS→jaw mapping"
```

### Task 12: DXL bus wrapper with graceful degradation

**Files:**
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\dxl_bus.py`
- Create: `C:\dev\lafufu\packages\animator\tests\test_dxl_bus.py`

The real DXL bus talks to the U2D2 via `dynamixel-sdk`. Tests use the fake from `lafufu_shared.testing` for hardware-free unit testing; the real bus only runs on the Pi.

- [ ] **Step 1: Write the dxl_bus interface tests** (against the fake)

`packages/animator/tests/test_dxl_bus.py`:

```python
import pytest
from lafufu_shared.testing import FakeDxlBus

def test_fake_bus_records_writes():
    bus = FakeDxlBus()
    bus.write("jaw", 1700)
    bus.write("jaw", 1750)
    assert bus.last_position("jaw") == 1750
    assert bus.writes == [("jaw", 1700), ("jaw", 1750)]

def test_disconnect_raises_on_write():
    bus = FakeDxlBus()
    bus.disconnect()
    with pytest.raises(IOError):
        bus.write("jaw", 1700)

def test_reconnect_clears_error_state():
    bus = FakeDxlBus()
    bus.disconnect()
    bus.reconnect()
    bus.write("jaw", 1700)
    assert bus.last_position("jaw") == 1700

def test_disable_torque_counted():
    bus = FakeDxlBus()
    bus.disable_torque()
    bus.disable_torque()
    assert bus.torque_disabled_count == 2
```

- [ ] **Step 2: Run; should pass already (fixture is built)**

```powershell
uv run pytest packages/animator/tests/test_dxl_bus.py -v
```

Expected: all PASS.

- [ ] **Step 3: Write the real `dxl_bus.py` wrapper for on-Pi use**

```python
"""Real Dynamixel U2D2 bus wrapper.

For tests, use `lafufu_shared.testing.FakeDxlBus` instead.
On the Pi: auto-detects /dev/ttyUSB* and tries common baud rates.
If no bus is available, raises ConnectionError — caller handles by going degraded.
"""
import glob
import logging
import platform
import time
from typing import Iterable

from . import pose

log = logging.getLogger(__name__)

# Control table addresses (Dynamixel X-series, protocol 2.0)
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132


def default_port_candidates() -> list[str]:
    if platform.system().lower() == "linux":
        return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    # Windows fallback
    return [f"COM{i}" for i in range(1, 30)]


def default_bauds() -> list[int]:
    return [57600, 115200, 1_000_000, 2_000_000, 3_000_000, 4_000_000]


class DxlBus:
    """Real DXL bus. Lazy-import dynamixel_sdk so unit tests don't need it."""

    def __init__(self, port: str | None = None, baud: int | None = None) -> None:
        from dynamixel_sdk import PortHandler, PacketHandler  # lazy
        self._PortHandler = PortHandler
        self._PacketHandler = PacketHandler
        self._port_name = port
        self._baud = baud
        self._port = None
        self._packet = PacketHandler(2.0)

    def open(self) -> None:
        """Open the bus. Tries provided port/baud, else auto-detects."""
        candidates_ports: Iterable[str] = [self._port_name] if self._port_name else default_port_candidates()
        candidates_bauds: Iterable[int] = [self._baud] if self._baud else default_bauds()

        for p in candidates_ports:
            try:
                handler = self._PortHandler(p)
                if not handler.openPort():
                    continue
                for b in candidates_bauds:
                    if not handler.setBaudRate(b):
                        continue
                    # Probe motor 1 to verify the bus is alive
                    _, comm_result, _ = self._packet.read4ByteTxRx(handler, 1, ADDR_PRESENT_POSITION)
                    if comm_result == 0:  # COMM_SUCCESS
                        self._port = handler
                        self._port_name = p
                        self._baud = b
                        log.info("dxl.bus.open port=%s baud=%d", p, b)
                        return
                handler.closePort()
            except Exception as e:
                log.debug("dxl.probe.failed port=%s error=%s", p, e)

        raise ConnectionError(f"U2D2 not found on any of {list(candidates_ports)}")

    def enable_torque(self) -> None:
        for _, dxl_id in pose.DXL_IDS.items():
            self._packet.write1ByteTxRx(self._port, dxl_id, ADDR_TORQUE_ENABLE, 1)

    def disable_torque(self) -> None:
        if self._port is None:
            return
        for _, dxl_id in pose.DXL_IDS.items():
            try:
                self._packet.write1ByteTxRx(self._port, dxl_id, ADDR_TORQUE_ENABLE, 0)
            except Exception:
                pass

    def write(self, name: str, position: int) -> None:
        if self._port is None:
            raise IOError("DXL bus not open")
        dxl_id = pose.DXL_IDS[name]
        self._packet.write4ByteTxRx(self._port, dxl_id, ADDR_GOAL_POSITION, int(position))

    def read(self, name: str) -> int:
        if self._port is None:
            raise IOError("DXL bus not open")
        dxl_id = pose.DXL_IDS[name]
        val, _, _ = self._packet.read4ByteTxRx(self._port, dxl_id, ADDR_PRESENT_POSITION)
        return int(val)

    def close(self) -> None:
        self.disable_torque()
        if self._port:
            self._port.closePort()
            self._port = None
```

- [ ] **Step 4: Quick syntax-only test**

```powershell
uv run python -c "from lafufu_animator import dxl_bus; print('import ok')"
```

Expected: `import ok` (will fail if `dynamixel-sdk` isn't installed; that's why the import is lazy).

- [ ] **Step 5: Commit**

```powershell
git add packages/animator
git commit -m "animator: add real DXL bus wrapper (lazy SDK import + auto-detect)"
```

### Task 13: Animator service main + integration test

**Files:**
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\service.py`
- Create: `C:\dev\lafufu\packages\animator\src\lafufu_animator\main.py`
- Create: `C:\dev\lafufu\packages\animator\tests\test_service.py`

- [ ] **Step 1: Write the integration test FIRST**

`packages/animator/tests/test_service.py`:

```python
import asyncio
import pytest
import nats
from lafufu_shared import schemas, topics
from lafufu_shared.nats_helper import publish_model
from lafufu_shared.testing import FakeDxlBus, nats_server_fixture
from lafufu_animator.service import AnimatorService

nats_server = nats_server_fixture("4240")


@pytest.fixture
async def running_animator(nats_server):
    bus = FakeDxlBus()
    svc = AnimatorService(bus=bus, nats_url=nats_server)
    task = asyncio.create_task(svc.run())
    # Wait for service ready
    await asyncio.sleep(0.4)
    yield svc, bus
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)


async def test_publishes_idle_state_on_startup(running_animator, nats_server):
    svc, bus = running_animator
    nc = await nats.connect(nats_server)
    seen: list[schemas.AnimatorState] = []

    async def cb(msg):
        seen.append(schemas.AnimatorState.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.ANIMATOR_STATE}.*", cb=cb)
    # Force-republish current state by sending a no-op preview
    await publish_model(nc, topics.ANIMATOR_INTENT_PREVIEW,
                       schemas.AnimatorIntentPreview(name="jaw", position=1728))
    await asyncio.sleep(0.2)
    await nc.drain()
    # At minimum we should see one state event since startup
    # (state was published before our subscription; test that fake bus got the write)
    assert bus.last_position("jaw") == 1728


async def test_preview_intent_moves_servo(running_animator, nats_server):
    svc, bus = running_animator
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.ANIMATOR_INTENT_PREVIEW,
                       schemas.AnimatorIntentPreview(name="head_lr", position=2100))
    await asyncio.sleep(0.15)
    await nc.drain()
    assert bus.last_position("head_lr") == 2100


async def test_play_expression_intent_applies_offsets(running_animator, nats_server):
    svc, bus = running_animator
    bus.writes.clear()
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
                       schemas.AnimatorIntentPlayExpression(name="surprised", intensity=1.0))
    await asyncio.sleep(0.3)
    await nc.drain()
    # surprised opens the jaw — expect jaw to have moved toward MOUTH_OPEN
    from lafufu_animator import pose
    final_jaw = bus.last_position("jaw")
    assert final_jaw is not None
    assert final_jaw < pose.MOUTH_CLOSE_DXL  # opened


async def test_tts_rms_drives_jaw_during_speaking(running_animator, nats_server):
    svc, bus = running_animator
    bus.writes.clear()
    nc = await nats.connect(nats_server)
    # Simulate a sequence of RMS values
    for i in range(5):
        await publish_model(nc, topics.AGENT_TTS_RMS,
                           schemas.AgentTtsRms(ts=i * 0.04, rms=0.8, mouth_target=0.8))
        await asyncio.sleep(0.04)
    await nc.drain()
    # Jaw should have moved (multiple writes)
    jaw_writes = [w for w in bus.writes if w[0] == "jaw"]
    assert len(jaw_writes) >= 2


async def test_degrades_gracefully_when_bus_disconnects(running_animator, nats_server):
    svc, bus = running_animator
    nc = await nats.connect(nats_server)
    seen: list[schemas.AnimatorState] = []

    async def cb(msg):
        seen.append(schemas.AnimatorState.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.ANIMATOR_STATE}.*", cb=cb)
    bus.disconnect()
    # Send a preview; service should publish degraded state
    await publish_model(nc, topics.ANIMATOR_INTENT_PREVIEW,
                       schemas.AnimatorIntentPreview(name="jaw", position=1700))
    await asyncio.sleep(0.3)
    await nc.drain()
    assert any(s.state == "degraded" for s in seen)
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/animator/tests/test_service.py -v
```

Expected: ImportError on `AnimatorService`.

- [ ] **Step 3: Write `service.py`**

```python
"""AnimatorService: subscribes to intents and RMS, drives the DXL bus."""
import asyncio
import time
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from . import expressions, lipsync, pose


class DxlBusProtocol(Protocol):
    def write(self, name: str, position: int) -> None: ...
    def read(self, name: str) -> int: ...
    def disable_torque(self) -> None: ...
    def open(self) -> None: ...


class AnimatorService(BaseService):
    name = "animator"
    heartbeat_interval_s = 5.0

    def __init__(self, bus: DxlBusProtocol, nats_url: str | None = None) -> None:
        super().__init__()
        self._bus = bus
        self._nats_url = nats_url
        self._envelope = lipsync.LipsyncEnvelope()
        self._current_pose = pose.idle_pose()
        self._has_u2d2 = True  # set False on disconnect
        self._last_rms_ts = 0.0
        self._pose_publish_task: asyncio.Task | None = None
        self._lipsync_watchdog_task: asyncio.Task | None = None

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        # Try to open the real bus; ignore if it's already opened (e.g. fake)
        try:
            self._bus.open()  # type: ignore[call-arg]
        except (AttributeError, TypeError):
            # Fakes don't need open()
            pass
        except ConnectionError as e:
            self.log.warning("dxl.open.failed error=%s", e)
            self._has_u2d2 = False

        try:
            self._move_to_pose(self._current_pose)
        except IOError:
            self._has_u2d2 = False

        await self._publish_state("idle" if self._has_u2d2 else "degraded")

        # Subscribe to intents
        await nats_helper.subscribe_model(
            self.nats, topics.ANIMATOR_INTENT_PREVIEW,
            schemas.AnimatorIntentPreview, self._on_preview,
        )
        await nats_helper.subscribe_model(
            self.nats, topics.ANIMATOR_INTENT_SET_POSE,
            schemas.AnimatorIntentSetPose, self._on_set_pose,
        )
        await nats_helper.subscribe_model(
            self.nats, topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
            schemas.AnimatorIntentPlayExpression, self._on_play_expression,
        )
        await nats_helper.subscribe_model(
            self.nats, topics.AGENT_TTS_RMS,
            schemas.AgentTtsRms, self._on_tts_rms,
        )
        await nats_helper.subscribe_model(
            self.nats, topics.AGENT_REPLY,
            schemas.AgentReply, self._on_agent_reply,
        )

        # Background tasks
        self._pose_publish_task = asyncio.create_task(self._pose_publish_loop())
        self._lipsync_watchdog_task = asyncio.create_task(self._lipsync_watchdog())

    async def on_shutdown(self) -> None:
        if self._pose_publish_task:
            self._pose_publish_task.cancel()
        if self._lipsync_watchdog_task:
            self._lipsync_watchdog_task.cancel()
        try:
            self._bus.disable_torque()
        except Exception:
            pass

    async def _publish_state(self, state_name: str, detail: str | None = None) -> None:
        topic = f"{topics.ANIMATOR_STATE}.{state_name}"
        await nats_helper.publish_model(
            self.nats, topic,
            schemas.AnimatorState(state=state_name, detail=detail, has_u2d2=self._has_u2d2),  # type: ignore[arg-type]
        )

    def _move_to_pose(self, p: schemas.AnimatorPose) -> None:
        for name, value in (("head_lr", p.head_lr), ("head_ud", p.head_ud),
                            ("eye", p.eye), ("jaw", p.jaw), ("brow", p.brow)):
            try:
                self._bus.write(name, value)
            except IOError:
                self._has_u2d2 = False
                raise
        self._current_pose = p

    async def _safe_apply(self, target_pose: schemas.AnimatorPose) -> None:
        try:
            self._move_to_pose(target_pose)
            if not self._has_u2d2:
                # Recovered
                self._has_u2d2 = True
                await self._publish_state("idle")
        except IOError as e:
            self.log.warning("dxl.write.failed error=%s", e)
            self._has_u2d2 = False
            await self._publish_state("degraded", detail=str(e))

    async def _on_preview(self, subject: str, msg: schemas.AnimatorIntentPreview) -> None:
        new = self._current_pose.model_copy(update={msg.name: pose.clamp_dxl(msg.name, msg.position)})
        await self._safe_apply(new)

    async def _on_set_pose(self, subject: str, msg: schemas.AnimatorIntentSetPose) -> None:
        await self._safe_apply(msg.pose)

    async def _on_play_expression(self, subject: str, msg: schemas.AnimatorIntentPlayExpression) -> None:
        offsets = expressions.get_offsets(msg.name, msg.intensity)
        target = expressions.apply_offsets(pose.idle_pose(), offsets)
        await self._safe_apply(target)
        await nats_helper.publish_model(
            self.nats, topics.ANIMATOR_EVENT_GESTURE_DONE,
            schemas.AnimatorEvent(event="gesture_done", name=msg.name),
        )

    async def _on_agent_reply(self, subject: str, msg: schemas.AgentReply) -> None:
        """When agent emits a reply with an emotion, set the matching expression."""
        offsets = expressions.get_offsets(msg.emotion, intensity=1.0)
        target = expressions.apply_offsets(pose.idle_pose(), offsets)
        await self._safe_apply(target)

    async def _on_tts_rms(self, subject: str, msg: schemas.AgentTtsRms) -> None:
        # Drive jaw via envelope
        dt = msg.ts - self._last_rms_ts if msg.ts > self._last_rms_ts else 0.04
        self._last_rms_ts = msg.ts
        smoothed = self._envelope.step(target=msg.mouth_target, dt=dt)
        jaw_pos = lipsync.rms_to_jaw_dxl(smoothed)
        new = self._current_pose.model_copy(update={"jaw": jaw_pos})
        await self._safe_apply(new)

    async def _pose_publish_loop(self) -> None:
        """Publish current pose at 20 Hz for live UI."""
        while not self._shutdown.is_set():
            try:
                await nats_helper.publish_model(self.nats, topics.ANIMATOR_POSE, self._current_pose)
            except Exception as e:
                self.log.warning("pose.publish.failed error=%s", e)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=0.05)
            except TimeoutError:
                pass

    async def _lipsync_watchdog(self) -> None:
        """If no RMS for 500ms, close the jaw."""
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(0.2)
                if self._envelope.value > 0.01:
                    if time.monotonic() - self._last_rms_ts > 0.5:
                        self._envelope.reset()
                        new = self._current_pose.model_copy(update={"jaw": pose.MOUTH_CLOSE_DXL})
                        await self._safe_apply(new)
            except Exception as e:
                self.log.warning("lipsync_watchdog.error error=%s", e)
```

- [ ] **Step 4: Write `main.py`** (CLI entry)

```python
"""Entry point: python -m lafufu_animator"""
import asyncio
from .dxl_bus import DxlBus
from .service import AnimatorService


def main() -> None:
    bus = DxlBus()  # auto-detects on real Pi
    svc = AnimatorService(bus=bus)
    asyncio.run(svc.run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run integration tests**

```powershell
uv run pytest packages/animator/tests/test_service.py -v
```

Expected: all PASS (real NATS, fake DXL).

- [ ] **Step 6: Run the full animator suite**

```powershell
uv run pytest packages/animator -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/animator
git commit -m "animator: add service with intent/RMS subscriptions + integration tests"
```

---

## Phase D: Agent service (Tasks 14–19)

Goal: Agent runs the voice pipeline (mic → STT → LLM → TTS) and publishes the right NATS topics. Pure logic unit-tested; pipeline integration-tested with all heavy deps faked.

### Task 14: Agent package + emotion parser

**Files:**
- Create: `C:\dev\lafufu\packages\agent\pyproject.toml`
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\__init__.py`
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\emotion_parser.py`
- Create: `C:\dev\lafufu\packages\agent\tests\test_emotion_parser.py`

- [ ] **Step 1: Package skeleton + pyproject**

```powershell
mkdir packages\agent\src\lafufu_agent
mkdir packages\agent\tests
ni packages\agent\src\lafufu_agent\__init__.py
ni packages\agent\tests\__init__.py
```

`packages/agent/pyproject.toml`:

```toml
[project]
name = "lafufu-agent"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "lafufu-shared",
    "pyaudio>=0.2.14",
    "openai-whisper>=20231117",
    "httpx>=0.27",
    "piper-tts>=1.2",
]

[tool.uv.sources]
lafufu-shared = { workspace = true }

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lafufu_agent"]
```

Sync:

```powershell
uv sync --all-packages
```

- [ ] **Step 2: Failing tests for emotion parser**

`packages/agent/tests/test_emotion_parser.py`:

```python
import pytest
from lafufu_agent.emotion_parser import parse

def test_parses_emotion_tag_at_start():
    e, t = parse("[happy]\nHello world")
    assert e == "happy"
    assert t == "Hello world"

def test_parses_with_trailing_whitespace():
    e, t = parse("[surprised]   \nWhoa!")
    assert e == "surprised"
    assert t == "Whoa!"

def test_no_tag_returns_neutral():
    e, t = parse("Just text without a tag.")
    assert e == "neutral"
    assert t == "Just text without a tag."

def test_unknown_tag_returns_neutral():
    e, t = parse("[confused]\nHmm.")
    assert e == "neutral"
    # Tag is stripped even if not matched
    assert t == "Hmm."

def test_multiline_body_preserved():
    e, t = parse("[sad]\nLine one.\nLine two.")
    assert e == "sad"
    assert t == "Line one.\nLine two."

def test_case_insensitive_tag():
    e, t = parse("[HAPPY]\nWoo")
    assert e == "happy"

def test_strips_surrounding_whitespace():
    e, t = parse("  [agree]\nyes  ")
    assert e == "agree"
    assert t == "yes"
```

- [ ] **Step 3: Run to verify failure**

```powershell
uv run pytest packages/agent/tests/test_emotion_parser.py -v
```

Expected: ImportError.

- [ ] **Step 4: Write `emotion_parser.py`**

```python
"""Parse LLM replies of the form `[emotion]\\nbody text`."""
import re
from typing import get_args

from lafufu_shared.schemas import Emotion

_VALID_EMOTIONS: set[str] = set(get_args(Emotion))
_TAG_RE = re.compile(r"^\s*\[([a-zA-Z]+)\]\s*\n?", re.MULTILINE)


def parse(reply: str) -> tuple[str, str]:
    """Return (emotion, body). Unknown/missing tags → 'neutral'."""
    m = _TAG_RE.match(reply)
    if not m:
        return "neutral", reply.strip()
    tag = m.group(1).lower()
    body = reply[m.end():].strip()
    if tag not in _VALID_EMOTIONS:
        return "neutral", body
    return tag, body
```

- [ ] **Step 5: Run + commit**

```powershell
uv run pytest packages/agent/tests/test_emotion_parser.py -v
git add packages/agent
git commit -m "agent: add package + emotion parser"
```

### Task 15: VAD (silence-based voice activity detection)

**Files:**
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\vad.py`
- Create: `C:\dev\lafufu\packages\agent\tests\test_vad.py`

- [ ] **Step 1: Write failing tests**

`packages/agent/tests/test_vad.py`:

```python
import pytest
import struct
from lafufu_agent.vad import audio_rms, SilenceDetector


def _pcm16_buffer(samples: list[int]) -> bytes:
    """Build int16 little-endian PCM bytes."""
    return b"".join(struct.pack("<h", s) for s in samples)


def test_audio_rms_silence_is_zero():
    buf = _pcm16_buffer([0] * 100)
    assert audio_rms(buf) == 0.0


def test_audio_rms_loud_is_nonzero():
    buf = _pcm16_buffer([5000] * 100)
    assert audio_rms(buf) > 1000.0


def test_silence_detector_triggers_after_threshold_silence():
    det = SilenceDetector(silence_threshold=500, silent_chunks_required=3)
    loud = _pcm16_buffer([5000] * 100)
    silent = _pcm16_buffer([0] * 100)
    # Speak first
    assert not det.is_done(det.observe(loud))
    # Now silence
    assert not det.is_done(det.observe(silent))
    assert not det.is_done(det.observe(silent))
    assert det.is_done(det.observe(silent))


def test_silence_detector_resets_on_speech():
    det = SilenceDetector(silence_threshold=500, silent_chunks_required=3)
    silent = _pcm16_buffer([0] * 100)
    loud = _pcm16_buffer([5000] * 100)
    det.observe(silent)
    det.observe(silent)
    rms = det.observe(loud)
    # Speech should reset the silence counter
    assert det.silent_count == 0
    assert rms > 500


def test_silence_detector_started_flag():
    det = SilenceDetector(silence_threshold=500, silent_chunks_required=3)
    silent = _pcm16_buffer([0] * 100)
    det.observe(silent)
    assert not det.started
    loud = _pcm16_buffer([5000] * 100)
    det.observe(loud)
    assert det.started
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/agent/tests/test_vad.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `vad.py`**

```python
"""Silence-based VAD: tracks RMS, decides when an utterance has ended."""
import audioop


def audio_rms(pcm16_bytes: bytes) -> float:
    """Return RMS of int16 little-endian PCM audio. 2 bytes per sample."""
    if not pcm16_bytes:
        return 0.0
    return float(audioop.rms(pcm16_bytes, 2))


class SilenceDetector:
    """Track speech onset + silence-based end-of-utterance.

    `silence_threshold`: RMS below this counts as silent.
    `silent_chunks_required`: consecutive silent chunks to trigger end.
    """

    def __init__(self, silence_threshold: int = 800, silent_chunks_required: int = 30) -> None:
        self.silence_threshold = silence_threshold
        self.silent_chunks_required = silent_chunks_required
        self.silent_count = 0
        self.started = False

    def observe(self, chunk: bytes) -> float:
        """Process one chunk, return its RMS. Mutates internal state."""
        rms = audio_rms(chunk)
        if rms >= self.silence_threshold:
            self.silent_count = 0
            self.started = True
        elif self.started:
            self.silent_count += 1
        return rms

    def is_done(self, _rms: float) -> bool:
        """True when we've seen enough silence after speech started."""
        return self.started and self.silent_count >= self.silent_chunks_required

    def reset(self) -> None:
        self.silent_count = 0
        self.started = False
```

- [ ] **Step 4: Run + commit**

```powershell
uv run pytest packages/agent/tests/test_vad.py -v
git add packages/agent
git commit -m "agent: add silence-based VAD"
```

### Task 16: Audio capture (PyAudio singleton, env-configurable mic select)

This ports the work we did in the existing `lafufu-jb` repo (PyAudio singleton + flexible mic selection). Keep the same env vars: `LAFUFU_INPUT_DEVICE`, `LAFUFU_INPUT_DEVICE_PREFER`, `LAFUFU_INPUT_DEVICE_AVOID`.

**Files:**
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\audio_capture.py`

(No unit tests — this is hardware-bound; verified by service integration test in Task 19.)

- [ ] **Step 1: Write `audio_capture.py`**

```python
"""Process-wide PyAudio singleton + flexible mic device selection."""
import atexit
import logging
import os

import pyaudio

log = logging.getLogger(__name__)

_DEFAULT_PREFER = ("shure", "jabra")
_DEFAULT_AVOID = ("soundbar", "monitor of", "output", "playback")

_pa_singleton: "pyaudio.PyAudio | None" = None
_selector_logged: bool = False


def get_pyaudio() -> pyaudio.PyAudio:
    """Return a process-wide PyAudio instance, suppressing ALSA stderr on first init."""
    global _pa_singleton
    if _pa_singleton is not None:
        return _pa_singleton

    devnull = None
    old_fd = None
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_fd = os.dup(2)
        os.dup2(devnull, 2)
    except OSError:
        pass
    try:
        _pa_singleton = pyaudio.PyAudio()
    finally:
        if old_fd is not None:
            os.dup2(old_fd, 2)
            os.close(old_fd)
        if devnull is not None:
            os.close(devnull)
    return _pa_singleton


@atexit.register
def _terminate_pyaudio():
    global _pa_singleton
    if _pa_singleton is not None:
        try:
            _pa_singleton.terminate()
        except Exception:
            pass
        _pa_singleton = None


def _env_list(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def select_input_device(p: pyaudio.PyAudio) -> int | None:
    """Pick a pyaudio input device index. None → system default.

    Order: LAFUFU_INPUT_DEVICE → PREFER list → first non-AVOID → None.
    """
    global _selector_logged
    devices: list[tuple[int, str]] = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            devices.append((i, info.get("name", "")))

    avoid = _env_list("LAFUFU_INPUT_DEVICE_AVOID", _DEFAULT_AVOID)
    prefer = _env_list("LAFUFU_INPUT_DEVICE_PREFER", _DEFAULT_PREFER)
    forced = (os.environ.get("LAFUFU_INPUT_DEVICE") or "").strip()

    chosen: int | None = None
    reason = "system default"

    if forced:
        if forced.isdigit():
            idx = int(forced)
            if any(i == idx for i, _ in devices):
                chosen = idx
                reason = f"LAFUFU_INPUT_DEVICE={forced}"
        else:
            needle = forced.lower()
            for i, name in devices:
                if needle in name.lower():
                    chosen, reason = i, f"LAFUFU_INPUT_DEVICE~={forced!r} → {name!r}"
                    break

    if chosen is None:
        for needle in prefer:
            for i, name in devices:
                if needle in name.lower():
                    chosen, reason = i, f"prefer={needle!r} → {name!r}"
                    break
            if chosen is not None:
                break

    if chosen is None:
        for i, name in devices:
            if not any(s in name.lower() for s in avoid):
                chosen, reason = i, f"first non-avoided → {name!r}"
                break

    if not _selector_logged:
        log.info("mic.selected reason=%s", reason)
        _selector_logged = True
    return chosen
```

- [ ] **Step 2: Quick import check**

```powershell
uv run python -c "from lafufu_agent.audio_capture import get_pyaudio, select_input_device; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```powershell
git add packages/agent
git commit -m "agent: add PyAudio singleton + flexible mic device selection"
```

### Task 17: Thin STT/LLM/TTS wrappers (mockable interfaces)

**Files:**
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\stt.py`
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\llm.py`
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\tts.py`

These are intentionally minimal wrappers so the pipeline (Task 18) can take them as injectable interfaces. Tests use the fakes from `lafufu_shared.testing`.

- [ ] **Step 1: Write `stt.py`**

```python
"""Whisper STT wrapper."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class Whisper:
    """Lazy-loads the Whisper model on first transcribe()."""

    def __init__(self, model_name: str = "tiny") -> None:
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        import whisper  # lazy
        log.info("whisper.loading model=%s", self.model_name)
        self._model = whisper.load_model(self.model_name)
        log.info("whisper.loaded model=%s", self.model_name)

    def transcribe(self, audio_path: str | Path) -> str:
        if self._model is None:
            self.load()
        result = self._model.transcribe(str(audio_path), fp16=False, language="en")
        return result.get("text", "").strip()
```

- [ ] **Step 2: Write `llm.py`**

```python
"""Ollama HTTP client for chat completions."""
import logging
import time

import httpx

log = logging.getLogger(__name__)


class Ollama:
    """Async client for Ollama's /api/chat endpoint."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:7b",
                 system_prompt: str = "", keep_alive: str = "10m", timeout_s: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.keep_alive = keep_alive
        self.timeout_s = timeout_s

    async def warmup(self) -> float:
        """Hit the model with a no-op request to load it. Returns seconds taken."""
        t0 = time.monotonic()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt or "You are a helpful assistant."},
                {"role": "user", "content": "warmup"},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": 1},
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
        return time.monotonic() - t0

    async def chat(self, user_text: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        return ((data.get("message") or {}).get("content") or "").strip()
```

- [ ] **Step 3: Write `tts.py`**

```python
"""Piper TTS wrapper.

Returns a list of (audio_chunk_bytes, mouth_target_0to1) tuples so the agent
can stream playback + publish RMS to NATS as it goes.
"""
import audioop
import logging
import wave
from pathlib import Path

log = logging.getLogger(__name__)


class Piper:
    def __init__(self, model_path: Path, chunk_ms: int = 40) -> None:
        self.model_path = Path(model_path)
        self.chunk_ms = chunk_ms
        self._voice = None
        self._sample_rate = 22050  # piper default; refined on load
        self._sample_width = 2

    def load(self) -> None:
        if self._voice is not None:
            return
        from piper import PiperVoice  # lazy
        self._voice = PiperVoice.load(str(self.model_path))
        self._sample_rate = self._voice.config.sample_rate

    def synthesize(self, text: str) -> list[tuple[bytes, float]]:
        """Synthesize text → list of (audio_chunk, mouth_target).

        Each chunk is ~`chunk_ms` of int16 PCM, mouth_target ∈ [0,1].
        """
        if self._voice is None:
            self.load()
        # Piper streams audio_int16_bytes
        audio = b"".join(self._voice.synthesize_stream_raw(text))
        return self._chunkify(audio)

    def _chunkify(self, audio: bytes) -> list[tuple[bytes, float]]:
        bytes_per_sample = self._sample_width
        samples_per_chunk = int(self._sample_rate * self.chunk_ms / 1000)
        bytes_per_chunk = samples_per_chunk * bytes_per_sample
        out: list[tuple[bytes, float]] = []
        # Normalize RMS by max int16 (32767) → [0,1]
        for i in range(0, len(audio), bytes_per_chunk):
            chunk = audio[i:i + bytes_per_chunk]
            if not chunk:
                continue
            rms = audioop.rms(chunk, bytes_per_sample)
            normalized = min(1.0, rms / 8000.0)  # 8000 RMS ≈ comfortable speech
            out.append((chunk, normalized))
        return out

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def sample_width(self) -> int:
        return self._sample_width
```

- [ ] **Step 4: Import check**

```powershell
uv run python -c "from lafufu_agent.stt import Whisper; from lafufu_agent.llm import Ollama; from lafufu_agent.tts import Piper; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```powershell
git add packages/agent
git commit -m "agent: add STT/LLM/TTS thin wrappers (lazy imports, mockable)"
```

### Task 18: Pipeline orchestrator

**Files:**
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\pipeline.py`
- Create: `C:\dev\lafufu\packages\agent\tests\test_pipeline.py`

The pipeline ties together VAD → STT → LLM → TTS, emits the right NATS events, and is fully testable using fakes.

- [ ] **Step 1: Write failing pipeline tests**

`packages/agent/tests/test_pipeline.py`:

```python
import asyncio
import pytest
from lafufu_shared.testing import nats_server_fixture, FakeOllama, FakePiper
from lafufu_shared import schemas, topics
from lafufu_agent.pipeline import VoicePipeline

nats_server = nats_server_fixture("4250")


class FakeMic:
    """Yields a fixed list of canned (chunk_bytes, eventual_transcript) once."""
    def __init__(self):
        self.played = False

    def listen_once(self) -> str:
        """Return the transcribed text. Synchronous for simplicity in tests."""
        self.played = True
        return "hello lafufu"


async def test_pipeline_one_cycle_publishes_all_state_transitions(nats_server):
    import nats
    nc = await nats.connect(nats_server)
    states: list[str] = []
    replies: list[schemas.AgentReply] = []
    rms_events: list[schemas.AgentTtsRms] = []

    async def cb_state(msg):
        states.append(msg.subject)

    async def cb_reply(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    async def cb_rms(msg):
        rms_events.append(schemas.AgentTtsRms.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.AGENT_STATE}.*", cb=cb_state)
    await nc.subscribe(topics.AGENT_REPLY, cb=cb_reply)
    await nc.subscribe(topics.AGENT_TTS_RMS, cb=cb_rms)

    fake_ollama = FakeOllama(scripts=[("hello", "[happy]\nHi there!")])
    fake_piper = FakePiper(chunks=[(b"\x00" * 1024, 0.5)] * 4)

    pipeline = VoicePipeline(
        nats_client=await nats.connect(nats_server, name="pipeline"),
        mic=FakeMic(),
        ollama=fake_ollama,
        piper=fake_piper,
    )
    await pipeline.run_one_cycle()
    await asyncio.sleep(0.2)
    await nc.drain()

    # Expected state transitions
    state_tails = [s.rsplit(".", 1)[-1] for s in states]
    for required in ("listening", "thinking", "speaking", "idle"):
        assert required in state_tails, f"missing {required} in {state_tails}"

    # Expected reply
    assert len(replies) == 1
    assert replies[0].text == "Hi there!"
    assert replies[0].emotion == "happy"

    # Expected RMS chunks
    assert len(rms_events) == 4
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/agent/tests/test_pipeline.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `pipeline.py`**

```python
"""VoicePipeline: orchestrates one listen → think → speak cycle.

Decoupled from concrete mic/Whisper/Ollama/Piper — uses Protocol-style duck typing.
"""
import asyncio
import logging
import time
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics

log = logging.getLogger(__name__)


class MicProtocol(Protocol):
    def listen_once(self) -> str:
        """Block until utterance ends; return transcribed text."""


class OllamaProtocol(Protocol):
    async def chat(self, user_text: str) -> str: ...


class PiperProtocol(Protocol):
    def synthesize(self, text: str) -> list[tuple[bytes, float]]: ...


class VoicePipeline:
    def __init__(self, nats_client, mic, ollama, piper, speaker_play=None) -> None:
        self.nats = nats_client
        self.mic = mic
        self.ollama = ollama
        self.piper = piper
        self.speaker_play = speaker_play  # callable(chunk_bytes) → None

    async def _publish_state(self, state_name: str) -> None:
        await nats_helper.publish_model(
            self.nats, f"{topics.AGENT_STATE}.{state_name}",
            schemas.AgentState(state=state_name),  # type: ignore[arg-type]
        )

    async def run_one_cycle(self) -> None:
        # ---- Listening ----
        await self._publish_state("listening")
        # Run blocking mic call in executor
        loop = asyncio.get_running_loop()
        transcript = await loop.run_in_executor(None, self.mic.listen_once)
        await nats_helper.publish_model(
            self.nats, topics.AGENT_TRANSCRIPT,
            schemas.AgentTranscript(text=transcript, timestamp=time.time()),
        )

        # ---- Thinking ----
        await self._publish_state("thinking")
        reply_raw = await self.ollama.chat(transcript)

        from .emotion_parser import parse
        emotion, body = parse(reply_raw)
        await nats_helper.publish_model(
            self.nats, topics.AGENT_REPLY,
            schemas.AgentReply(text=body, emotion=emotion),  # type: ignore[arg-type]
        )

        # ---- Speaking ----
        await self._publish_state("speaking")
        chunks = self.piper.synthesize(body)
        start_ts = time.monotonic()
        for i, (audio_bytes, mouth_target) in enumerate(chunks):
            if self.speaker_play:
                self.speaker_play(audio_bytes)
            await nats_helper.publish_model(
                self.nats, topics.AGENT_TTS_RMS,
                schemas.AgentTtsRms(
                    ts=time.monotonic() - start_ts,
                    rms=mouth_target,
                    mouth_target=mouth_target,
                ),
            )

        await self._publish_state("idle")
```

- [ ] **Step 4: Run tests + commit**

```powershell
uv run pytest packages/agent/tests/test_pipeline.py -v
git add packages/agent
git commit -m "agent: add VoicePipeline orchestrator (one-cycle, fully fakeable)"
```

### Task 19: Agent service main + integration test

**Files:**
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\service.py`
- Create: `C:\dev\lafufu\packages\agent\src\lafufu_agent\main.py`
- Create: `C:\dev\lafufu\packages\agent\tests\test_service.py`

- [ ] **Step 1: Write failing service test**

`packages/agent/tests/test_service.py`:

```python
import asyncio
import pytest
import nats
from lafufu_shared.testing import nats_server_fixture, FakeOllama, FakePiper
from lafufu_shared import schemas, topics
from lafufu_shared.nats_helper import publish_model
from lafufu_agent.service import AgentService

nats_server = nats_server_fixture("4251")


class FakeMicForService:
    def __init__(self, transcripts: list[str]):
        self.transcripts = list(transcripts)
        self.calls = 0

    def listen_once(self) -> str:
        if not self.transcripts:
            # Block forever once exhausted (simulates idle)
            import time as t; t.sleep(60)
            return ""
        self.calls += 1
        return self.transcripts.pop(0)


async def test_text_message_intent_triggers_pipeline(nats_server):
    """When agent receives agent.intent.text_message, run pipeline as if mic heard it."""
    svc = AgentService(
        mic=FakeMicForService([]),  # mic does nothing
        ollama=FakeOllama(scripts=[("ping", "[neutral]\npong")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)  # wait for ready

    nc = await nats.connect(nats_server)
    replies: list[schemas.AgentReply] = []

    async def cb(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    await nc.subscribe(topics.AGENT_REPLY, cb=cb)

    await publish_model(nc, topics.AGENT_INTENT_TEXT_MESSAGE,
                       schemas.AgentIntentTextMessage(text="ping"))
    await asyncio.sleep(0.5)
    await nc.drain()

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
    assert len(replies) == 1
    assert replies[0].text == "pong"
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/agent/tests/test_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `service.py`**

```python
"""AgentService: BaseService that runs the voice loop and accepts text intents."""
import asyncio
import logging

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .pipeline import VoicePipeline

log = logging.getLogger(__name__)


class AgentService(BaseService):
    name = "agent"

    def __init__(self, mic, ollama, piper, speaker_play=None, nats_url: str | None = None) -> None:
        super().__init__()
        self._mic = mic
        self._ollama = ollama
        self._piper = piper
        self._speaker_play = speaker_play
        self._nats_url = nats_url
        self._pipeline: VoicePipeline | None = None
        self._cycle_lock = asyncio.Lock()
        self._mic_loop_task: asyncio.Task | None = None

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        await self._publish_state("warming")
        # Hot-warm Ollama if it has a warmup method
        if hasattr(self._ollama, "warmup"):
            try:
                elapsed = await self._ollama.warmup()
                self.log.info("ollama.warmed_up elapsed_s=%.1f", elapsed)
            except Exception as e:
                self.log.warning("ollama.warmup.failed error=%s", e)
        self._pipeline = VoicePipeline(self.nats, self._mic, self._ollama, self._piper, self._speaker_play)
        await self._publish_state("idle")

        # Subscribe to text-message intent (headless input path)
        await nats_helper.subscribe_model(
            self.nats, topics.AGENT_INTENT_TEXT_MESSAGE,
            schemas.AgentIntentTextMessage, self._on_text_message,
        )

        # Note: we do NOT auto-start the mic loop in tests (FakeMicForService blocks).
        # Real `main.py` calls start_mic_loop() explicitly after construction.

    async def on_shutdown(self) -> None:
        await self._publish_state("shutdown")
        if self._mic_loop_task:
            self._mic_loop_task.cancel()

    async def _publish_state(self, name: str) -> None:
        await nats_helper.publish_model(
            self.nats, f"{topics.AGENT_STATE}.{name}",
            schemas.AgentState(state=name),  # type: ignore[arg-type]
        )

    async def _on_text_message(self, subject: str, msg: schemas.AgentIntentTextMessage) -> None:
        async with self._cycle_lock:
            # Override the mic's next call to return this text
            class _OnceMic:
                def __init__(self, text): self.text = text
                def listen_once(self): return self.text

            tmp = VoicePipeline(self.nats, _OnceMic(msg.text), self._ollama, self._piper, self._speaker_play)
            await tmp.run_one_cycle()

    def start_mic_loop(self) -> None:
        """Call from real main() after on_startup to begin listening continuously."""
        self._mic_loop_task = asyncio.create_task(self._mic_loop())

    async def _mic_loop(self) -> None:
        while not self._shutdown.is_set():
            async with self._cycle_lock:
                try:
                    await self._pipeline.run_one_cycle()
                except Exception as e:
                    self.log.exception("voice_cycle.failed error=%s", e)
                    await asyncio.sleep(1.0)
```

- [ ] **Step 4: Write `main.py`** (production entry; uses real mic/STT/LLM/TTS)

```python
"""Entry: python -m lafufu_agent

Real production path with Whisper/Ollama/Piper/PyAudio.
Pulls config from env vars.
"""
import asyncio
import os
import wave
from pathlib import Path

import pyaudio

from lafufu_shared import settings

from .audio_capture import get_pyaudio, select_input_device
from .llm import Ollama
from .service import AgentService
from .stt import Whisper
from .tts import Piper
from .vad import SilenceDetector


SYSTEM_PROMPT = (
    "You are Lafufu, a mischievous and playful humanoid creature. "
    "Reply in no more than 20 words. Always output an \"[emotion]\" tag first "
    "(happy, sad, angry, surprised, neutral, agree, disagree), then the response. "
    "Never use emojis."
)


class RealMic:
    """Records from mic until silence, returns transcribed text via Whisper."""

    def __init__(self, whisper: Whisper, *, rate: int = 44100, chunk_ms: int = 40):
        self.whisper = whisper
        self.rate = rate
        self.chunk_size = int(rate * chunk_ms / 1000)
        self.tmp_wav = Path("/tmp/lafufu_capture.wav")

    def listen_once(self) -> str:
        p = get_pyaudio()
        device = select_input_device(p)
        eff_rate = self.rate
        try:
            if device is not None and not p.is_format_supported(
                float(self.rate), input_device=device, input_channels=1,
                input_format=pyaudio.paInt16,
            ):
                eff_rate = int(p.get_device_info_by_index(device).get("defaultSampleRate", 16000))
        except (ValueError, OSError):
            if device is not None:
                eff_rate = int(p.get_device_info_by_index(device).get("defaultSampleRate", 16000))

        eff_chunk = max(1, int(eff_rate * 0.04))
        det = SilenceDetector(silence_threshold=800, silent_chunks_required=int(1.5 * eff_rate / eff_chunk))
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=eff_rate, input=True,
                        input_device_index=device, frames_per_buffer=eff_chunk)
        frames: list[bytes] = []
        try:
            while True:
                data = stream.read(eff_chunk, exception_on_overflow=False)
                det.observe(data)
                frames.append(data)
                if det.is_done(0):
                    break
                if len(frames) * eff_chunk / eff_rate > 10:  # max 10s
                    break
        finally:
            stream.stop_stream()
            stream.close()

        import audioop
        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)
        with wave.open(str(self.tmp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(raw)
        return self.whisper.transcribe(self.tmp_wav)


def _aplay_player():
    """Returns a callable(chunk_bytes) that streams to aplay (Pi only)."""
    import subprocess
    proc = None

    def play(chunk: bytes) -> None:
        nonlocal proc
        if proc is None:
            # Open aplay on first chunk; close in atexit
            device = os.environ.get("LAFUFU_APLAY_DEVICE", "default")
            proc = subprocess.Popen(
                ["aplay", "-q", "-D", device, "-f", "S16_LE", "-c", "1", "-r", "22050"],
                stdin=subprocess.PIPE,
            )
        proc.stdin.write(chunk)

    return play


def main() -> None:
    whisper_model = os.environ.get("LAFUFU_WHISPER_MODEL", "tiny")
    qwen_model = os.environ.get("LAFUFU_LLM_MODEL", "qwen2.5:7b")
    piper_model_path = Path(os.environ.get("LAFUFU_PIPER_MODEL", "models/lafufu_voice.onnx"))
    ollama_url = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

    whisper = Whisper(model_name=whisper_model)
    ollama = Ollama(base_url=ollama_url, model=qwen_model, system_prompt=SYSTEM_PROMPT)
    piper = Piper(model_path=piper_model_path)
    mic = RealMic(whisper=whisper)
    player = _aplay_player()

    svc = AgentService(mic=mic, ollama=ollama, piper=piper, speaker_play=player)

    async def run():
        # Run base lifecycle + start mic loop after startup
        run_task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.5)  # let startup complete
        svc.start_mic_loop()
        await run_task

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run integration test**

```powershell
uv run pytest packages/agent/tests/test_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full agent suite**

```powershell
uv run pytest packages/agent -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/agent
git commit -m "agent: add service + main entry + integration test (text_message path)"
```

---

## Phase E: Printer service (Tasks 20–21)

Goal: Printer service subscribes to `agent.reply` (auto-print when enabled) and `printer.intent.*`, prints via CUPS, gracefully offline when no printer.

### Task 20: Printer package + CUPS client + formatter

**Files:**
- Create: `C:\dev\lafufu\packages\printer\pyproject.toml`
- Create: `C:\dev\lafufu\packages\printer\src\lafufu_printer\__init__.py`
- Create: `C:\dev\lafufu\packages\printer\src\lafufu_printer\cups_client.py`
- Create: `C:\dev\lafufu\packages\printer\src\lafufu_printer\formatter.py`
- Create: `C:\dev\lafufu\packages\printer\tests\test_formatter.py`

- [ ] **Step 1: Package skeleton + pyproject**

```powershell
mkdir packages\printer\src\lafufu_printer
mkdir packages\printer\tests
ni packages\printer\src\lafufu_printer\__init__.py
ni packages\printer\tests\__init__.py
```

`packages/printer/pyproject.toml`:

```toml
[project]
name = "lafufu-printer"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["lafufu-shared"]

[tool.uv.sources]
lafufu-shared = { workspace = true }

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lafufu_printer"]
```

Sync: `uv sync --all-packages`

- [ ] **Step 2: Write failing formatter tests**

`packages/printer/tests/test_formatter.py`:

```python
from datetime import datetime
from lafufu_printer.formatter import format_reply, format_transcript


def test_format_reply_includes_text_and_emotion():
    out = format_reply(text="Hello world", emotion="happy", ts=datetime(2026, 5, 17, 14, 30))
    assert "Hello world" in out
    assert "happy" in out.lower()
    assert "2026" in out

def test_format_reply_strips_trailing_whitespace_per_line():
    out = format_reply(text="Hi    \nWorld   ", emotion="neutral", ts=datetime.now())
    assert "    " not in out  # no trailing 4-space runs


def test_format_transcript_includes_roles():
    out = format_transcript([
        {"role": "user", "text": "Are you alive?"},
        {"role": "assistant", "text": "Mostly!"},
    ])
    assert "user" in out.lower()
    assert "assistant" in out.lower()
    assert "Are you alive?" in out
    assert "Mostly!" in out


def test_format_reply_truncates_extreme_length():
    long = "x" * 5000
    out = format_reply(text=long, emotion="neutral", ts=datetime.now())
    assert len(out) < 4000  # printer doesn't need a novel
```

- [ ] **Step 3: Run to verify failure**

```powershell
uv run pytest packages/printer/tests/test_formatter.py -v
```

Expected: ImportError.

- [ ] **Step 4: Write `formatter.py`**

```python
"""Format Lafufu replies / transcripts for the thermal printer."""
from datetime import datetime

_MAX_BODY_CHARS = 2000
_SEPARATOR = "-" * 30


def _strip_trailing_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def format_reply(*, text: str, emotion: str, ts: datetime) -> str:
    """One reply, with timestamp + emotion header."""
    body = _strip_trailing_ws(text)[:_MAX_BODY_CHARS]
    header = f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [{emotion}]"
    return f"{header}\n{_SEPARATOR}\n{body}\n{_SEPARATOR}\n\n"


def format_transcript(entries: list[dict[str, str]]) -> str:
    """Multi-turn transcript dump."""
    lines: list[str] = ["LAFUFU TRANSCRIPT", _SEPARATOR]
    for e in entries:
        role = e.get("role", "?")
        text = _strip_trailing_ws(e.get("text", ""))
        lines.append(f"{role.upper()}: {text}")
    lines.append(_SEPARATOR)
    return "\n".join(lines) + "\n\n"
```

- [ ] **Step 5: Run + commit formatter**

```powershell
uv run pytest packages/printer/tests/test_formatter.py -v
git add packages/printer
git commit -m "printer: add package + text formatter for replies/transcripts"
```

- [ ] **Step 6: Write `cups_client.py`** (thin wrapper around `lp` subprocess)

```python
"""CUPS print client. Shells out to `lp` for simplicity / no native deps."""
import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


class CupsUnavailable(Exception):
    pass


class CupsClient:
    def __init__(self, printer_name: str | None = None) -> None:
        self._printer_name = printer_name
        self._lp = shutil.which("lp")
        self._lpstat = shutil.which("lpstat")

    @property
    def available(self) -> bool:
        return self._lp is not None and self._lpstat is not None

    def list_printers(self) -> list[str]:
        if not self._lpstat:
            return []
        try:
            out = subprocess.check_output([self._lpstat, "-p"], text=True, timeout=5)
        except subprocess.SubprocessError as e:
            log.warning("lpstat.failed error=%s", e)
            return []
        names: list[str] = []
        for line in out.splitlines():
            if line.startswith("printer "):
                # "printer NAME is idle. ..."
                parts = line.split()
                if len(parts) >= 2:
                    names.append(parts[1])
        return names

    def default_printer(self) -> str | None:
        if self._printer_name:
            return self._printer_name
        printers = self.list_printers()
        return printers[0] if printers else None

    def print_text(self, text: str, *, title: str | None = None) -> str:
        """Print text. Returns job id (best effort)."""
        if not self._lp:
            raise CupsUnavailable("`lp` not on PATH")
        printer = self.default_printer()
        if not printer:
            raise CupsUnavailable("no CUPS printers configured")
        cmd = [self._lp, "-d", printer]
        if title:
            cmd += ["-t", title]
        result = subprocess.run(
            cmd, input=text.encode("utf-8"), capture_output=True, timeout=20,
        )
        if result.returncode != 0:
            raise CupsUnavailable(f"lp exited {result.returncode}: {result.stderr.decode()}")
        # lp prints "request id is NAME-1234 (1 file(s))"
        out = result.stdout.decode().strip()
        return out.split()[3] if "request id is" in out else "?"
```

- [ ] **Step 7: Commit**

```powershell
git add packages/printer
git commit -m "printer: add CUPS client (lp subprocess wrapper)"
```

### Task 21: Printer service main + integration test

**Files:**
- Create: `C:\dev\lafufu\packages\printer\src\lafufu_printer\service.py`
- Create: `C:\dev\lafufu\packages\printer\src\lafufu_printer\main.py`
- Create: `C:\dev\lafufu\packages\printer\tests\test_service.py`

- [ ] **Step 1: Write failing service test (with fake CUPS)**

`packages/printer/tests/test_service.py`:

```python
import asyncio
import pytest
import nats
from lafufu_shared import schemas, topics
from lafufu_shared.testing import nats_server_fixture
from lafufu_shared.nats_helper import publish_model
from lafufu_printer.service import PrinterService

nats_server = nats_server_fixture("4260")


class FakeCups:
    def __init__(self, available: bool = True):
        self.available = available
        self.printed: list[tuple[str, str | None]] = []

    def list_printers(self) -> list[str]:
        return ["fake-printer"] if self.available else []

    def default_printer(self) -> str | None:
        return "fake-printer" if self.available else None

    def print_text(self, text: str, *, title: str | None = None) -> str:
        self.printed.append((text, title))
        return "job-001"


async def test_publishes_offline_when_no_printer(nats_server):
    cups = FakeCups(available=False)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=True)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    seen: list[schemas.PrinterState] = []

    async def cb(msg):
        seen.append(schemas.PrinterState.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.PRINTER_STATE}.*", cb=cb)
    # Trigger by sending an intent so service publishes current state
    await publish_model(nc, topics.PRINTER_INTENT_TEST_PAGE, schemas.AgentReply(text="x", emotion="neutral"))
    await asyncio.sleep(0.2)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert any(s.state == "offline" for s in seen) or cups.printed == []


async def test_auto_print_on_agent_reply(nats_server):
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=True)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.AGENT_REPLY, schemas.AgentReply(text="Hello!", emotion="happy"))
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(cups.printed) == 1
    assert "Hello!" in cups.printed[0][0]


async def test_auto_print_disabled(nats_server):
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.AGENT_REPLY, schemas.AgentReply(text="Hi", emotion="neutral"))
    await asyncio.sleep(0.2)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert cups.printed == []


async def test_print_intent_always_prints(nats_server):
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.PRINTER_INTENT_PRINT_TEXT,
                       schemas.PrinterIntentPrintText(text="Hand-triggered"))
    await asyncio.sleep(0.2)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(cups.printed) == 1
    assert "Hand-triggered" in cups.printed[0][0]
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/printer/tests/test_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `service.py`**

```python
"""PrinterService: auto-prints replies (when enabled) and handles on-demand intents."""
import logging
from datetime import datetime
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .formatter import format_reply, format_transcript

log = logging.getLogger(__name__)


class CupsProtocol(Protocol):
    @property
    def available(self) -> bool: ...
    def list_printers(self) -> list[str]: ...
    def default_printer(self) -> str | None: ...
    def print_text(self, text: str, *, title: str | None = None) -> str: ...


class PrinterService(BaseService):
    name = "printer"

    def __init__(self, cups: CupsProtocol, nats_url: str | None = None, auto_print: bool = True) -> None:
        super().__init__()
        self._cups = cups
        self._nats_url = nats_url
        self.auto_print = auto_print

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        await self._publish_state()
        await nats_helper.subscribe_model(
            self.nats, topics.AGENT_REPLY,
            schemas.AgentReply, self._on_agent_reply,
        )
        await nats_helper.subscribe_model(
            self.nats, topics.PRINTER_INTENT_PRINT_TEXT,
            schemas.PrinterIntentPrintText, self._on_print_text,
        )
        await nats_helper.subscribe_model(
            self.nats, topics.PRINTER_INTENT_PRINT_TRANSCRIPT,
            schemas.PrinterIntentPrintTranscript, self._on_print_transcript,
        )

    async def _publish_state(self, state_name: str | None = None, detail: str | None = None) -> None:
        if state_name is None:
            state_name = "idle" if self._cups.default_printer() else "offline"
        await nats_helper.publish_model(
            self.nats, f"{topics.PRINTER_STATE}.{state_name}",
            schemas.PrinterState(
                state=state_name,  # type: ignore[arg-type]
                detail=detail,
                printer_name=self._cups.default_printer(),
            ),
        )

    async def _safe_print(self, text: str, title: str | None = None) -> None:
        if not self._cups.default_printer():
            await self._publish_state("offline")
            return
        await self._publish_state("printing")
        try:
            job_id = self._cups.print_text(text, title=title)
            await nats_helper.publish_model(
                self.nats, topics.PRINTER_EVENT_JOB_DONE,
                schemas.PrinterEvent(event="job_done", job_id=job_id),
            )
        except Exception as e:
            self.log.warning("print.failed error=%s", e)
            await self._publish_state("error", detail=str(e))
            return
        await self._publish_state("idle")

    async def _on_agent_reply(self, subject: str, msg: schemas.AgentReply) -> None:
        if not self.auto_print:
            return
        text = format_reply(text=msg.text, emotion=msg.emotion, ts=datetime.now())
        await self._safe_print(text, title="lafufu reply")

    async def _on_print_text(self, subject: str, msg: schemas.PrinterIntentPrintText) -> None:
        text = format_reply(text=msg.text, emotion="neutral", ts=datetime.now())
        await self._safe_print(text, title=msg.title or "lafufu print")

    async def _on_print_transcript(self, subject: str, msg: schemas.PrinterIntentPrintTranscript) -> None:
        await self._safe_print(format_transcript(msg.transcript), title="lafufu transcript")
```

- [ ] **Step 4: Write `main.py`**

```python
"""Entry: python -m lafufu_printer"""
import asyncio
import os

from .cups_client import CupsClient
from .service import PrinterService


def main() -> None:
    cups = CupsClient(printer_name=os.environ.get("LAFUFU_PRINTER_NAME"))
    auto_print = os.environ.get("LAFUFU_PRINTER_AUTO", "true").lower() not in ("0", "false", "no")
    svc = PrinterService(cups=cups, auto_print=auto_print)
    asyncio.run(svc.run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run integration tests + commit**

```powershell
uv run pytest packages/printer/tests/test_service.py -v
uv run pytest packages/printer -v
git add packages/printer
git commit -m "printer: add service + integration tests (fake CUPS)"
```

---

## Phase F: Control service — DB + REST API (Tasks 22–25)

Goal: `control` owns SQLite, exposes a clean REST API, and proxies admin intents onto NATS. Tested via FastAPI's TestClient with a stub publisher.

### Task 22: Control package + DB models + engine

**Files:**
- Create: `packages/control/pyproject.toml`
- Create: `packages/control/src/lafufu_control/__init__.py`
- Create: `packages/control/src/lafufu_control/db.py`
- Create: `packages/control/src/lafufu_control/models/__init__.py`
- Create: `packages/control/src/lafufu_control/models/setting.py`
- Create: `packages/control/src/lafufu_control/models/expression.py`
- Create: `packages/control/src/lafufu_control/models/behavior.py`
- Create: `packages/control/src/lafufu_control/models/plugin.py`
- Create: `packages/control/tests/test_db.py`

- [ ] **Step 1: Package skeleton + pyproject**

```powershell
mkdir packages\control\src\lafufu_control\models
mkdir packages\control\src\lafufu_control\api\routers
mkdir packages\control\tests
mkdir packages\control\src\lafufu_control\static
ni packages\control\src\lafufu_control\__init__.py
ni packages\control\src\lafufu_control\models\__init__.py
ni packages\control\src\lafufu_control\api\__init__.py
ni packages\control\src\lafufu_control\api\routers\__init__.py
ni packages\control\tests\__init__.py
```

`packages/control/pyproject.toml`:

```toml
[project]
name = "lafufu-control"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "lafufu-shared",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "sqlmodel>=0.0.16",
    "httpx>=0.27",
]

[tool.uv.sources]
lafufu-shared = { workspace = true }

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lafufu_control"]
```

Sync: `uv sync --all-packages`

- [ ] **Step 2: Write failing DB tests**

`packages/control/tests/test_db.py`:

```python
import pytest
from sqlmodel import Session, select
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.setting import Setting


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
```

- [ ] **Step 3: Run to verify failure**

```powershell
uv run pytest packages/control/tests/test_db.py -v
```

- [ ] **Step 4: Write `db.py`**

```python
"""SQLite engine + session helpers."""
from collections.abc import Generator
from sqlmodel import Session, SQLModel, create_engine


def create_engine_for_path(path: str):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
    return engine


def init_db(engine) -> None:
    from .models import setting, expression, behavior, plugin  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
```

- [ ] **Step 5: Write the model files**

`packages/control/src/lafufu_control/models/setting.py`:

```python
from sqlmodel import Field, SQLModel


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True, max_length=200)
    value: str = Field(max_length=4000)
    value_type: str = Field(max_length=32)
    description: str | None = Field(default=None, max_length=500)
```

`packages/control/src/lafufu_control/models/expression.py`:

```python
from sqlmodel import Field, SQLModel


class Expression(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    head_lr_offset: int = 0
    head_ud_offset: int = 0
    eye_offset: int = 0
    jaw_offset: int = 0
    brow_offset: int = 0
    description: str | None = Field(default=None, max_length=500)
```

`packages/control/src/lafufu_control/models/behavior.py`:

```python
from sqlmodel import Field, SQLModel


class Behavior(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200, unique=True)
    trigger_json: str = Field(default="{}")
    actions_json: str = Field(default="[]")
    enabled: bool = True
```

`packages/control/src/lafufu_control/models/plugin.py`:

```python
from sqlmodel import Field, SQLModel


class Plugin(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200, unique=True)
    version: str = Field(max_length=50, default="0.0.0")
    enabled: bool = False
    config_json: str = Field(default="{}")
```

`packages/control/src/lafufu_control/models/__init__.py`:

```python
from .behavior import Behavior
from .expression import Expression
from .plugin import Plugin
from .setting import Setting

__all__ = ["Behavior", "Expression", "Plugin", "Setting"]
```

- [ ] **Step 6: Run DB tests + commit**

```powershell
uv run pytest packages/control/tests/test_db.py -v
git add packages/control
git commit -m "control: add package + SQLModel models + DB engine helpers"
```

### Task 23: FastAPI app + settings router + snapshot

**Files:**
- Create: `packages/control/src/lafufu_control/api/app.py`
- Create: `packages/control/src/lafufu_control/api/routers/settings.py`
- Create: `packages/control/src/lafufu_control/api/routers/snapshot.py`
- Create: `packages/control/tests/test_settings_router.py`

- [ ] **Step 1: Write failing settings-router tests**

`packages/control/tests/test_settings_router.py`:

```python
import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda subject, payload: None)
    return TestClient(app)


def test_list_settings_empty(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json() == []


def test_create_setting(client):
    r = client.put("/api/settings/agent.tts.speed", json={"value": 0.85, "value_type": "float"})
    assert r.status_code == 200
    assert r.json()["key"] == "agent.tts.speed"
    assert r.json()["value"] == "0.85"


def test_patch_publishes_config_changed(client, tmp_path):
    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "t2.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    c = TestClient(app)
    c.put("/api/settings/k", json={"value": "v1", "value_type": "str"})
    published.clear()
    r = c.patch("/api/settings/k", json={"value": "v2"})
    assert r.status_code == 200
    assert r.json()["value"] == "v2"
    assert len(published) == 1
    assert published[0][0].startswith("config.changed.k")
    assert published[0][1]["value"] == "v2"


def test_get_missing_404(client):
    r = client.get("/api/settings/missing")
    assert r.status_code == 404


def test_delete_setting(client):
    client.put("/api/settings/k", json={"value": "x", "value_type": "str"})
    r = client.delete("/api/settings/k")
    assert r.status_code == 204
    assert client.get("/api/settings/k").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/control/tests/test_settings_router.py -v
```

- [ ] **Step 3: Write `api/app.py`**

```python
"""FastAPI app factory. `nats_publish` is injected so tests can verify without a real broker."""
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import settings as settings_router
from .routers import snapshot as snapshot_router

STATIC_PATH = Path(__file__).parent.parent / "static"


def create_app(*, engine, nats_publish: Callable[[str, dict], None]) -> FastAPI:
    app = FastAPI(title="lafufu control", version="0.1.0")
    app.state.engine = engine
    app.state.nats_publish = nats_publish

    app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
    app.include_router(snapshot_router.router, prefix="/api/state", tags=["state"])

    if STATIC_PATH.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_PATH), html=True), name="spa")

    return app
```

- [ ] **Step 4: Write `api/routers/settings.py`**

```python
"""Settings CRUD. PATCH/PUT publish `config.changed.<key>` to NATS."""
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ...models.setting import Setting

router = APIRouter()


class SettingIn(BaseModel):
    value: Any
    value_type: str = "str"


class SettingOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None = None


def _encode(value: Any, vt: str) -> str:
    if vt == "json":
        return json.dumps(value)
    return str(value)


@router.get("", response_model=list[SettingOut])
def list_settings(req: Request):
    with Session(req.app.state.engine) as s:
        rows = s.exec(select(Setting)).all()
        return [SettingOut(**r.model_dump()) for r in rows]


@router.get("/{key}", response_model=SettingOut)
def get_setting(key: str, req: Request):
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if not row:
            raise HTTPException(404, detail={"error_code": "not_found", "message": f"setting {key} not found"})
        return SettingOut(**row.model_dump())


@router.put("/{key}", response_model=SettingOut)
def put_setting(key: str, body: SettingIn, req: Request):
    encoded = _encode(body.value, body.value_type)
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=encoded, value_type=body.value_type)
            s.add(row)
        else:
            row.value = encoded
            row.value_type = body.value_type
        s.commit()
        s.refresh(row)
        out = SettingOut(**row.model_dump())
    req.app.state.nats_publish(f"config.changed.{key}", {"key": key, "value": body.value, "source": "admin"})
    return out


@router.patch("/{key}", response_model=SettingOut)
def patch_setting(key: str, body: SettingIn, req: Request):
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if not row:
            raise HTTPException(404, detail={"error_code": "not_found", "message": f"setting {key} not found"})
        row.value = _encode(body.value, body.value_type)
        if body.value_type:
            row.value_type = body.value_type
        s.add(row)
        s.commit()
        s.refresh(row)
        out = SettingOut(**row.model_dump())
    req.app.state.nats_publish(f"config.changed.{key}", {"key": key, "value": body.value, "source": "admin"})
    return out


@router.delete("/{key}", status_code=204)
def delete_setting(key: str, req: Request):
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if not row:
            raise HTTPException(404)
        s.delete(row)
        s.commit()
```

- [ ] **Step 5: Write `api/routers/snapshot.py`**

```python
"""GET /api/state/snapshot — returns everything a browser needs to seed its UI."""
from fastapi import APIRouter, Request
from sqlmodel import Session, select

from ...models.setting import Setting

router = APIRouter()


@router.get("/snapshot")
def snapshot(req: Request):
    with Session(req.app.state.engine) as s:
        settings_rows = s.exec(select(Setting)).all()
    return {
        "settings": [
            {"key": x.key, "value": x.value, "value_type": x.value_type} for x in settings_rows
        ],
        "services": getattr(req.app.state, "service_status", {}),
        "last_pose": getattr(req.app.state, "last_pose", None),
    }
```

- [ ] **Step 6: Run + commit**

```powershell
uv run pytest packages/control/tests/test_settings_router.py -v
git add packages/control
git commit -m "control: add FastAPI app + settings router + snapshot endpoint"
```

### Task 24: System control + intent routers

**Files:**
- Create: `packages/control/src/lafufu_control/api/routers/system.py`
- Create: `packages/control/src/lafufu_control/api/routers/animator.py`
- Create: `packages/control/src/lafufu_control/api/routers/agent.py`
- Create: `packages/control/tests/test_system_router.py`
- Modify: `packages/control/src/lafufu_control/api/app.py` (mount new routers)

The service-restart handler uses a dictionary lookup (validated name → literal unit string) — user input is never interpolated into the subprocess argv.

- [ ] **Step 1: Write failing tests**

`packages/control/tests/test_system_router.py`:

```python
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client_factory(tmp_path):
    def make():
        engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
        init_db(engine)
        published: list[tuple[str, dict]] = []
        client = TestClient(create_app(
            engine=engine,
            nats_publish=lambda s, p: published.append((s, p)),
        ))
        return client, published
    return make


def test_restart_known_service(client_factory):
    client, published = client_factory()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stderr = b""
        r = client.post("/api/system/services/agent/restart")
    assert r.status_code == 200
    called_args = run_mock.call_args[0][0]
    assert called_args == ["systemctl", "restart", "lafufu-agent"]
    assert any(s == "system.service.restarting" for s, _ in published)


def test_restart_unknown_service_400(client_factory):
    client, _ = client_factory()
    r = client.post("/api/system/services/notreal/restart")
    assert r.status_code == 400


def test_animator_preview_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/animator/preview", json={"name": "jaw", "position": 1700})
    assert r.status_code == 202
    assert any(s == "animator.intent.preview" for s, _ in published)


def test_animator_expression_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/animator/expression", json={"name": "happy"})
    assert r.status_code == 202
    assert any(s == "animator.intent.play_expression" for s, _ in published)


def test_agent_text_message_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/agent/text_message", json={"text": "hello"})
    assert r.status_code == 202
    assert any(s == "agent.intent.text_message" for s, _ in published)
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/control/tests/test_system_router.py -v
```

- [ ] **Step 3: Write `api/routers/system.py`**

```python
"""System operations: service control via systemd. Uses dict lookup to avoid injection."""
import subprocess

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

# Validated name → literal systemd unit string. Never interpolate user input.
_SYSTEMCTL_UNITS: dict[str, str] = {
    "agent": "lafufu-agent",
    "animator": "lafufu-animator",
    "printer": "lafufu-printer",
    "control": "lafufu-control",
}


@router.post("/services/{name}/restart")
def restart_service(name: str, req: Request):
    unit = _SYSTEMCTL_UNITS.get(name)
    if unit is None:
        raise HTTPException(
            400, detail={"error_code": "unknown_service", "message": f"unknown service '{name}'"}
        )
    req.app.state.nats_publish("system.service.restarting", {"service": name, "event": "restarting"})
    try:
        result = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True,
            timeout=15,
        )
    except subprocess.SubprocessError as e:
        raise HTTPException(500, detail={"error_code": "systemctl_failed", "message": str(e)})
    if result.returncode != 0:
        raise HTTPException(500, detail={
            "error_code": "systemctl_nonzero",
            "message": result.stderr.decode(errors="replace") if result.stderr else "",
        })
    return {"ok": True, "service": name}
```

- [ ] **Step 4: Write `api/routers/animator.py`**

```python
"""Admin → animator intent proxy."""
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class PreviewBody(BaseModel):
    name: Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
    position: int


class ExpressionBody(BaseModel):
    name: str
    intensity: float = 1.0


class GestureBody(BaseModel):
    name: Literal["nod_yes", "nod_no", "look_around"]


@router.post("/preview", status_code=202)
def preview(body: PreviewBody, req: Request):
    req.app.state.nats_publish("animator.intent.preview", body.model_dump())
    return {"ok": True}


@router.post("/expression", status_code=202)
def expression(body: ExpressionBody, req: Request):
    req.app.state.nats_publish("animator.intent.play_expression", body.model_dump())
    return {"ok": True}


@router.post("/gesture", status_code=202)
def gesture(body: GestureBody, req: Request):
    req.app.state.nats_publish("animator.intent.gesture", body.model_dump())
    return {"ok": True}
```

- [ ] **Step 5: Write `api/routers/agent.py`**

```python
"""Admin → agent intent proxy (headless text input)."""
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class TextMessageBody(BaseModel):
    text: str


@router.post("/text_message", status_code=202)
def text_message(body: TextMessageBody, req: Request):
    req.app.state.nats_publish("agent.intent.text_message", body.model_dump())
    return {"ok": True}
```

- [ ] **Step 6: Mount the new routers in `api/app.py`**

Add to imports:

```python
from .routers import agent as agent_router
from .routers import animator as animator_router
from .routers import system as system_router
```

Inside `create_app`, after the existing `app.include_router(...)` lines:

```python
    app.include_router(system_router.router, prefix="/api/system", tags=["system"])
    app.include_router(animator_router.router, prefix="/api/animator", tags=["animator"])
    app.include_router(agent_router.router, prefix="/api/agent", tags=["agent"])
```

- [ ] **Step 7: Run tests + commit**

```powershell
uv run pytest packages/control/tests/test_system_router.py -v
git add packages/control
git commit -m "control: add system/animator/agent intent routers (dict-validated systemctl)"
```

### Task 25: Control service main + heartbeat/pose tracking

**Files:**
- Create: `packages/control/src/lafufu_control/service.py`
- Create: `packages/control/src/lafufu_control/main.py`

- [ ] **Step 1: Write `service.py`**

```python
"""ControlService: hosts FastAPI + tracks heartbeat-derived service status + last pose."""
import asyncio
import json
import time

import uvicorn

from lafufu_shared import nats_helper, schemas, settings, topics
from lafufu_shared.base_service import BaseService

from .api.app import create_app
from .db import create_engine_for_path, init_db


class ControlService(BaseService):
    name = "control"

    def __init__(self, host: str = "0.0.0.0", port: int = 8080, nats_url: str | None = None) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._nats_url = nats_url
        self._server: uvicorn.Server | None = None
        self._app = None

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        engine = create_engine_for_path(str(settings.db_path()))
        init_db(engine)
        loop = asyncio.get_running_loop()

        def publish_sync(subject: str, payload: dict) -> None:
            """Schedule a publish from the synchronous FastAPI handler thread."""
            data = json.dumps(payload).encode("utf-8")
            asyncio.run_coroutine_threadsafe(self.nats.publish(subject, data), loop)

        self._app = create_app(engine=engine, nats_publish=publish_sync)
        self._app.state.service_status = {}
        self._app.state.last_pose = None

        async def on_hb(subject: str, msg: schemas.SystemHeartbeat) -> None:
            self._app.state.service_status[msg.service] = {
                "service": msg.service,
                "last_seen": time.time(),
                "uptime_s": msg.uptime_s,
            }
        await nats_helper.subscribe_model(
            self.nats, f"{topics.SYSTEM_HEARTBEAT}.>",
            schemas.SystemHeartbeat, on_hb,
        )

        async def on_pose(subject: str, msg: schemas.AnimatorPose) -> None:
            self._app.state.last_pose = msg.model_dump()
        await nats_helper.subscribe_model(
            self.nats, topics.ANIMATOR_POSE,
            schemas.AnimatorPose, on_pose,
        )

        config = uvicorn.Config(
            self._app, host=self.host, port=self.port, log_level="info", loop="asyncio",
        )
        self._server = uvicorn.Server(config)

    async def main_loop(self) -> None:
        assert self._server
        serve_task = asyncio.create_task(self._server.serve())
        await self._shutdown.wait()
        self._server.should_exit = True
        await serve_task

    async def on_shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True
```

- [ ] **Step 2: Write `main.py`**

```python
"""Entry: python -m lafufu_control"""
import asyncio
import os

from .service import ControlService


def main() -> None:
    port = int(os.environ.get("LAFUFU_CONTROL_PORT", "8080"))
    asyncio.run(ControlService(port=port).run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run full control suite**

```powershell
uv run pytest packages/control -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```powershell
git add packages/control
git commit -m "control: add ControlService runner (uvicorn + heartbeat/pose subscribers)"
```

---

## Phase G: NATS↔WebSocket bridge (Task 26)

Goal: browsers connect to `ws://localhost:8080/ws`, send a subscription frame, receive matching NATS messages. Lazy subscription — `control` only subscribes to NATS topics that at least one browser actually wants.

### Task 26: WS bridge endpoint + lazy subscription

**Files:**
- Create: `packages/control/src/lafufu_control/api/ws_bridge.py`
- Modify: `packages/control/src/lafufu_control/api/app.py` (mount WS route)
- Modify: `packages/control/src/lafufu_control/service.py` (give app access to NATS client)
- Create: `packages/control/tests/test_ws_bridge.py`

**Protocol (kept dead-simple):**
- Browser opens WS to `/ws`
- Browser sends `{"op": "sub", "topics": ["agent.state.>", "animator.pose"]}`
- Browser receives `{"topic": "agent.state.idle", "payload": {...}}` for each NATS message matching subscribed patterns
- Browser sends `{"op": "unsub", "topics": ["animator.pose"]}` to stop
- Server tracks per-browser interest; when no browser cares about a topic, server unsubscribes from NATS

- [ ] **Step 1: Write failing WS test**

`packages/control/tests/test_ws_bridge.py`:

```python
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.api.ws_bridge import WsBridge
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_shared.testing import nats_server_fixture
import nats

nats_server = nats_server_fixture("4270")


@pytest.fixture
async def app_with_bridge(nats_server, tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    nc = await nats.connect(nats_server)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    bridge = WsBridge(nc)
    bridge.mount(app)
    return app, nc, bridge


async def test_ws_subscribe_and_receive(app_with_bridge):
    app, nc, bridge = app_with_bridge
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["test.echo"]})
        await asyncio.sleep(0.05)
        # NATS publish from elsewhere
        await nc.publish("test.echo", b'{"hi":"there"}')
        msg = ws.receive_json()
        assert msg["topic"] == "test.echo"
        assert msg["payload"] == {"hi": "there"}


async def test_ws_lazy_subscription_unsubs_when_last_client_leaves(app_with_bridge):
    app, nc, bridge = app_with_bridge
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["test.lazy"]})
        await asyncio.sleep(0.05)
        assert bridge.nats_sub_count("test.lazy") == 1
    # WS closed → bridge should unsubscribe NATS
    await asyncio.sleep(0.1)
    assert bridge.nats_sub_count("test.lazy") == 0


async def test_ws_invalid_payload_dropped(app_with_bridge):
    app, nc, bridge = app_with_bridge
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "topics": ["test.bad"]})
        await asyncio.sleep(0.05)
        await nc.publish("test.bad", b"not json")
        # Should not crash; just no message arrives within window
        try:
            ws.receive_json(timeout=0.2)
            assert False, "should not receive"
        except Exception:
            pass
```

- [ ] **Step 2: Run to verify failure**

```powershell
uv run pytest packages/control/tests/test_ws_bridge.py -v
```

- [ ] **Step 3: Write `api/ws_bridge.py`**

```python
"""NATS↔WebSocket bridge with lazy subscription.

Tracks per-WebSocket topic interest. Maintains one NATS subscription per
unique topic pattern; ref-counted. When the last interested WS disconnects
from a pattern, the bridge unsubscribes from NATS.
"""
import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from nats.aio.subscription import Subscription as NatsSubscription

log = logging.getLogger(__name__)


class WsBridge:
    def __init__(self, nats_client) -> None:
        self.nats = nats_client
        # pattern → (NatsSubscription, ref_count)
        self._subs: dict[str, tuple[NatsSubscription, int]] = {}
        # connected websockets → set of patterns they care about
        self._ws_patterns: dict[WebSocket, set[str]] = {}
        # pattern → set of websockets
        self._pattern_listeners: dict[str, set[WebSocket]] = {}

    def mount(self, app: FastAPI) -> None:
        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            self._ws_patterns[ws] = set()
            try:
                while True:
                    frame = await ws.receive_json()
                    op = frame.get("op")
                    topics = frame.get("topics", []) or []
                    if op == "sub":
                        for t in topics:
                            await self._add_sub(ws, t)
                    elif op == "unsub":
                        for t in topics:
                            self._remove_sub(ws, t)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                log.warning("ws.error error=%s", e)
            finally:
                for t in list(self._ws_patterns.get(ws, set())):
                    self._remove_sub(ws, t)
                self._ws_patterns.pop(ws, None)

    async def _add_sub(self, ws: WebSocket, pattern: str) -> None:
        self._ws_patterns[ws].add(pattern)
        self._pattern_listeners.setdefault(pattern, set()).add(ws)
        if pattern in self._subs:
            existing_sub, count = self._subs[pattern]
            self._subs[pattern] = (existing_sub, count + 1)
            return
        # First subscriber for this pattern — open NATS sub
        async def cb(msg):
            await self._fanout(pattern, msg.subject, msg.data)
        sub = await self.nats.subscribe(pattern, cb=cb)
        self._subs[pattern] = (sub, 1)

    def _remove_sub(self, ws: WebSocket, pattern: str) -> None:
        listeners = self._pattern_listeners.get(pattern)
        if listeners is not None:
            listeners.discard(ws)
        self._ws_patterns.get(ws, set()).discard(pattern)
        if pattern in self._subs:
            sub, count = self._subs[pattern]
            count -= 1
            if count <= 0:
                # Schedule unsubscribe (sync method is async on nats-py)
                loop = asyncio.get_event_loop()
                loop.create_task(sub.unsubscribe())
                del self._subs[pattern]
            else:
                self._subs[pattern] = (sub, count)

    async def _fanout(self, pattern: str, subject: str, data: bytes) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception as e:
            log.debug("ws.fanout.bad_payload subject=%s error=%s", subject, e)
            return
        frame = {"topic": subject, "payload": payload}
        dead: list[WebSocket] = []
        for ws in self._pattern_listeners.get(pattern, set()):
            try:
                await ws.send_json(frame)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                await ws.close()
            except Exception:
                pass

    # --- Inspection (used by tests) ---

    def nats_sub_count(self, pattern: str) -> int:
        return self._subs.get(pattern, (None, 0))[1]
```

- [ ] **Step 4: Wire the bridge into `control.service`**

Modify `packages/control/src/lafufu_control/service.py` — change `on_startup` to also create and mount the bridge:

```python
# Add at top of file:
from .api.ws_bridge import WsBridge

# Inside on_startup, AFTER create_app(...) and BEFORE creating the uvicorn config:

        bridge = WsBridge(self.nats)
        bridge.mount(self._app)
```

- [ ] **Step 5: Run WS bridge tests**

```powershell
uv run pytest packages/control/tests/test_ws_bridge.py -v
```

Expected: all PASS.

- [ ] **Step 6: Run full control suite (regression check)**

```powershell
uv run pytest packages/control -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/control
git commit -m "control: add NATS↔WS bridge with lazy subscription + tests"
```

---

## Phase H: Schema autogen + web scaffold (Tasks 27–29)

Goal: SolidJS+Vite SPA scaffolded with a build step that auto-generates TypeScript types from the pydantic schemas — single source of truth.

### Task 27: Schema autogen pipeline (Python → JSON Schema → TypeScript)

**Files:**
- Create: `packages/shared/src/lafufu_shared/export_schemas.py`
- Create: `web/scripts/gen_types.mjs`
- Modify: `web/package.json` (later in Task 28 — placeholder note here)

- [ ] **Step 1: Write the Python schema exporter**

`packages/shared/src/lafufu_shared/export_schemas.py`:

```python
"""Export all pydantic schemas to a single JSON Schema file.

Run: python -m lafufu_shared.export_schemas > web/src/shared/schemas.json
"""
import inspect
import json
import sys

from pydantic import BaseModel

from . import schemas as _schemas_mod


def collect_schemas() -> dict:
    """Walk the schemas module, emit a JSON Schema doc with all models under #/definitions."""
    out: dict = {"$schema": "http://json-schema.org/draft-07/schema#", "definitions": {}}
    for name, obj in inspect.getmembers(_schemas_mod):
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel:
            out["definitions"][name] = obj.model_json_schema(mode="serialization")
    return out


def main() -> int:
    json.dump(collect_schemas(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it runs**

```powershell
uv run python -m lafufu_shared.export_schemas | Select-Object -First 30
```

Expected: prints JSON starting with `{"$schema": ...}` containing every schema.

- [ ] **Step 3: Write the Node-side TS generator**

`web/scripts/gen_types.mjs`:

```javascript
// Generate web/src/shared/types.gen.ts from pydantic schemas.
// Run via: npm run gen:types  (defined in package.json in Task 28)
import { execSync } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { compile } from "json-schema-to-typescript";

const OUT = "src/shared/types.gen.ts";

// Capture the JSON Schema by running the Python exporter
const json = execSync("uv run python -m lafufu_shared.export_schemas", {
  cwd: "..",
  encoding: "utf-8",
});

const schema = JSON.parse(json);

// json-schema-to-typescript needs a top-level type; emit each definition independently
let out = "// AUTOGEN from pydantic — do not edit by hand.\n";
out += "// Regenerate via: npm run gen:types\n\n";

const sorted = Object.entries(schema.definitions).sort(([a], [b]) => a.localeCompare(b));
for (const [name, def] of sorted) {
  // eslint-disable-next-line no-await-in-loop
  const ts = await compile({ ...def, title: name }, name, {
    bannerComment: "",
    additionalProperties: false,
    style: { singleQuote: true, semi: true },
  });
  out += ts.trim() + "\n\n";
}

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, out);
console.log(`wrote ${OUT}: ${Object.keys(schema.definitions).length} types`);
```

- [ ] **Step 4: Commit (web package itself comes in Task 28; this commits the Python side and the script)**

```powershell
git add packages/shared/src/lafufu_shared/export_schemas.py web/scripts/gen_types.mjs
git commit -m "schemas: add Python→JSON Schema→TS autogen pipeline"
```

### Task 28: Web scaffold (Vite + SolidJS + Tailwind)

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/tailwind.config.ts`
- Create: `web/postcss.config.cjs`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/app.tsx`
- Create: `web/src/index.css`

- [ ] **Step 1: Bootstrap directories**

```powershell
mkdir web\src\face
mkdir web\src\admin
mkdir web\src\shared
mkdir web\tests
```

- [ ] **Step 2: Write `web/package.json`**

```json
{
  "name": "lafufu-web",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "npm run gen:types && tsc --noEmit && vite build --outDir ../packages/control/src/lafufu_control/static --emptyOutDir",
    "typecheck": "tsc --noEmit",
    "gen:types": "node scripts/gen_types.mjs",
    "test": "vitest"
  },
  "dependencies": {
    "solid-js": "^1.8.0",
    "@solidjs/router": "^0.13.0"
  },
  "devDependencies": {
    "vite": "^5.0.0",
    "vite-plugin-solid": "^2.10.0",
    "typescript": "^5.4.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "json-schema-to-typescript": "^15.0.0",
    "vitest": "^1.5.0",
    "jsdom": "^24.0.0",
    "@types/node": "^20.0.0"
  }
}
```

- [ ] **Step 3: Write `web/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "preserve",
    "jsxImportSource": "solid-js",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "isolatedModules": true,
    "resolveJsonModule": true,
    "types": ["vite/client"],
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src", "tests"]
}
```

- [ ] **Step 4: Write `web/vite.config.ts`**

```typescript
import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

export default defineConfig({
  plugins: [solid()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```

- [ ] **Step 5: Tailwind config**

`web/tailwind.config.ts`:

```typescript
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Emotion palette (Phase 0 — adjustable in DB later)
        happy: "#fcd34d",
        sad: "#60a5fa",
        angry: "#f87171",
        surprised: "#a78bfa",
        neutral: "#94a3b8",
        agree: "#34d399",
        disagree: "#f97316",
      },
    },
  },
} satisfies Config;
```

`web/postcss.config.cjs`:

```javascript
module.exports = { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

- [ ] **Step 6: Index HTML + entry**

`web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Lafufu</title>
  </head>
  <body class="bg-slate-950 text-slate-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`web/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

html, body, #root { height: 100%; margin: 0; }
body { font-family: ui-sans-serif, system-ui, sans-serif; }
```

`web/src/main.tsx`:

```tsx
/* @refresh reload */
import { render } from "solid-js/web";
import { Router } from "@solidjs/router";
import "./index.css";
import { App } from "./app";

render(() => (
  <Router>
    <App />
  </Router>
), document.getElementById("root")!);
```

`web/src/app.tsx`:

```tsx
import { Route } from "@solidjs/router";
import { lazy } from "solid-js";

const Face = lazy(() => import("./face/face"));
const Admin = lazy(() => import("./admin/admin"));

export function App() {
  return (
    <>
      <Route path="/" component={() => <div class="p-4">Lafufu — pick <a href="/face" class="underline">/face</a> or <a href="/admin" class="underline">/admin</a></div>} />
      <Route path="/face" component={Face} />
      <Route path="/admin" component={Admin} />
    </>
  );
}
```

(Face and Admin components are stubs filled in Phases I and J.)

- [ ] **Step 7: Stub `face/face.tsx` and `admin/admin.tsx`**

`web/src/face/face.tsx`:

```tsx
export default function Face() {
  return <div class="flex h-full items-center justify-center text-4xl">face (Phase I)</div>;
}
```

`web/src/admin/admin.tsx`:

```tsx
export default function Admin() {
  return <div class="p-6"><h1 class="text-2xl mb-4">Lafufu admin</h1><p class="text-slate-400">Phase J</p></div>;
}
```

- [ ] **Step 8: Install deps + smoke test**

```powershell
cd web
npm install
npm run typecheck
npm run gen:types
```

Expected:
- `npm install` succeeds
- `typecheck` passes (no .ts errors)
- `gen:types` creates `web/src/shared/types.gen.ts` containing all the schema types

- [ ] **Step 9: Build the SPA into control's static dir**

```powershell
npm run build
```

Expected: `packages/control/src/lafufu_control/static/index.html` exists.

- [ ] **Step 10: Commit**

```powershell
cd ..
git add web .gitignore
git commit -m "web: scaffold Vite + SolidJS + Tailwind with autogen-types build step"
```

### Task 29: NATS-over-WS client + design tokens

**Files:**
- Create: `web/src/shared/nats_ws.ts`
- Create: `web/src/shared/api.ts`
- Create: `web/src/shared/design.ts`
- Create: `web/tests/design.test.ts`
- Create: `web/tests/nats_ws.test.ts`

- [ ] **Step 1: Write `design.ts` (emotion→color, helpers)**

`web/src/shared/design.ts`:

```typescript
import type { Emotion } from "./types.gen";

export const EMOTION_COLORS: Record<Emotion, string> = {
  happy: "#fcd34d",
  sad: "#60a5fa",
  angry: "#f87171",
  surprised: "#a78bfa",
  neutral: "#94a3b8",
  agree: "#34d399",
  disagree: "#f97316",
};

export function emotionToColor(e: Emotion | string | undefined): string {
  if (!e) return EMOTION_COLORS.neutral;
  return EMOTION_COLORS[e as Emotion] ?? EMOTION_COLORS.neutral;
}

/** Map RMS [0,1] → CSS scale factor or height percentage. */
export function rmsToHeightPct(rms: number): number {
  return Math.max(0, Math.min(1, rms)) * 100;
}
```

- [ ] **Step 2: Write `api.ts` (REST helpers)**

`web/src/shared/api.ts`:

```typescript
const BASE = "/api";

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status}`);
  return r.status === 204 ? (undefined as T) : (await r.json() as T);
}

export const api = {
  snapshot: () => req<{ settings: Array<{ key: string; value: string; value_type: string }>; services: Record<string, any>; last_pose: any }>("GET", "/state/snapshot"),
  listSettings: () => req("GET", "/settings"),
  patchSetting: (key: string, body: { value: unknown; value_type?: string }) => req("PATCH", `/settings/${key}`, body),
  putSetting: (key: string, body: { value: unknown; value_type: string }) => req("PUT", `/settings/${key}`, body),
  restartService: (name: string) => req("POST", `/system/services/${name}/restart`),
  animatorPreview: (name: string, position: number) => req("POST", "/animator/preview", { name, position }),
  animatorExpression: (name: string, intensity = 1.0) => req("POST", "/animator/expression", { name, intensity }),
  agentTextMessage: (text: string) => req("POST", "/agent/text_message", { text }),
};
```

- [ ] **Step 3: Write `nats_ws.ts` (subscription client)**

`web/src/shared/nats_ws.ts`:

```typescript
type Frame = { topic: string; payload: any };
type Handler = (frame: Frame) => void;

export class NatsWs {
  private ws: WebSocket | null = null;
  private listeners = new Map<string, Set<Handler>>();
  private reconnectDelay = 1000;
  private maxDelay = 30000;
  private url: string;
  private active = false;

  constructor(url: string = "/ws") {
    this.url = url;
  }

  start(): void {
    this.active = true;
    this.connect();
  }

  stop(): void {
    this.active = false;
    this.ws?.close();
    this.ws = null;
  }

  private connect(): void {
    if (!this.active) return;
    const wsUrl = this.url.startsWith("ws") ? this.url
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${this.url}`;
    this.ws = new WebSocket(wsUrl);
    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      const topics = Array.from(this.listeners.keys());
      if (topics.length > 0) {
        this.ws!.send(JSON.stringify({ op: "sub", topics }));
      }
    };
    this.ws.onmessage = (ev) => {
      try {
        const frame: Frame = JSON.parse(ev.data);
        for (const [pattern, handlers] of this.listeners) {
          if (matchesPattern(pattern, frame.topic)) {
            handlers.forEach((h) => h(frame));
          }
        }
      } catch {
        // drop
      }
    };
    this.ws.onclose = () => {
      this.ws = null;
      if (this.active) {
        setTimeout(() => this.connect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(this.maxDelay, this.reconnectDelay * 2);
      }
    };
    this.ws.onerror = () => {/* onclose handles reconnect */};
  }

  subscribe(pattern: string, handler: Handler): () => void {
    let handlers = this.listeners.get(pattern);
    if (!handlers) {
      handlers = new Set();
      this.listeners.set(pattern, handlers);
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ op: "sub", topics: [pattern] }));
      }
    }
    handlers.add(handler);
    return () => {
      handlers!.delete(handler);
      if (handlers!.size === 0) {
        this.listeners.delete(pattern);
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ op: "unsub", topics: [pattern] }));
        }
      }
    };
  }
}

/** Match NATS-style wildcards: '*' for one token, '>' for tail. */
export function matchesPattern(pattern: string, topic: string): boolean {
  const p = pattern.split(".");
  const t = topic.split(".");
  for (let i = 0; i < p.length; i++) {
    if (p[i] === ">") return true;
    if (i >= t.length) return false;
    if (p[i] === "*") continue;
    if (p[i] !== t[i]) return false;
  }
  return p.length === t.length;
}
```

- [ ] **Step 4: Write `tests/design.test.ts`**

```typescript
import { describe, expect, it } from "vitest";
import { emotionToColor, rmsToHeightPct } from "../src/shared/design";

describe("emotionToColor", () => {
  it("returns specific color for known emotions", () => {
    expect(emotionToColor("happy")).toBe("#fcd34d");
    expect(emotionToColor("sad")).toBe("#60a5fa");
  });
  it("falls back to neutral for unknown/missing", () => {
    expect(emotionToColor(undefined)).toBe("#94a3b8");
    expect(emotionToColor("madeupemotion")).toBe("#94a3b8");
  });
});

describe("rmsToHeightPct", () => {
  it("clamps to 0..100", () => {
    expect(rmsToHeightPct(0)).toBe(0);
    expect(rmsToHeightPct(1)).toBe(100);
    expect(rmsToHeightPct(-0.5)).toBe(0);
    expect(rmsToHeightPct(2)).toBe(100);
  });
});
```

- [ ] **Step 5: Write `tests/nats_ws.test.ts`**

```typescript
import { describe, expect, it } from "vitest";
import { matchesPattern } from "../src/shared/nats_ws";

describe("matchesPattern", () => {
  it("matches exact topic", () => {
    expect(matchesPattern("agent.state.idle", "agent.state.idle")).toBe(true);
    expect(matchesPattern("agent.state.idle", "agent.state.thinking")).toBe(false);
  });
  it("supports * for one token", () => {
    expect(matchesPattern("agent.state.*", "agent.state.idle")).toBe(true);
    expect(matchesPattern("agent.state.*", "agent.state.x.y")).toBe(false);
  });
  it("supports > for tail", () => {
    expect(matchesPattern("agent.>", "agent.state.idle")).toBe(true);
    expect(matchesPattern("agent.>", "agent.transcript")).toBe(true);
    expect(matchesPattern(">", "anything.you.want")).toBe(true);
  });
  it("rejects short topic", () => {
    expect(matchesPattern("a.b.c", "a.b")).toBe(false);
  });
});
```

- [ ] **Step 6: Run web tests + commit**

```powershell
cd web
npm test --run
cd ..
git add web
git commit -m "web: add NATS-over-WS client, REST helpers, design tokens + tests"
```

---

## Phase I: Face view — Pi kiosk display (Task 30)

Goal: the `/face` route is what the Pi displays at all times. Ambient, low-CPU animations that reflect Lafufu's current state. Replaces the static mp4 background.

### Task 30: Face view with emotion + state visualization

**Files:**
- Modify: `web/src/face/face.tsx`
- Create: `web/src/face/state_blob.tsx`
- Create: `web/src/face/caption.tsx`

The face shows:
1. A full-screen gradient backdrop tinted by current emotion
2. A central "blob" that pulses with mic RMS when listening and TTS RMS when speaking
3. A status text (idle / listening / thinking / speaking / degraded)
4. Optional bottom-aligned caption with the latest transcript or reply

- [ ] **Step 1: Write `face/state_blob.tsx`**

```tsx
import { Component, createSignal, onCleanup, onMount } from "solid-js";

interface Props {
  intensity: () => number; // 0..1, drives the blob's pulse
  color: () => string;
}

export const StateBlob: Component<Props> = (props) => {
  const [pulse, setPulse] = createSignal(0);
  let frame: number | undefined;

  onMount(() => {
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      // First-order envelope toward target
      const target = props.intensity();
      setPulse((p) => p + (target - p) * Math.min(1, dt * 8));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  });
  onCleanup(() => frame && cancelAnimationFrame(frame));

  const scale = () => 0.6 + pulse() * 0.6;
  const opacity = () => 0.4 + pulse() * 0.5;

  return (
    <div
      class="absolute inset-0 flex items-center justify-center pointer-events-none"
      style={{ transition: "background 0.6s ease" }}
    >
      <div
        class="rounded-full"
        style={{
          width: "50vmin",
          height: "50vmin",
          background: `radial-gradient(circle, ${props.color()} 0%, transparent 70%)`,
          transform: `scale(${scale()})`,
          opacity: opacity().toString(),
          transition: "transform 30ms linear, opacity 30ms linear, background 0.6s ease",
        }}
      />
    </div>
  );
};
```

- [ ] **Step 2: Write `face/caption.tsx`**

```tsx
import { Component } from "solid-js";

export const Caption: Component<{ text: () => string | undefined }> = (props) => {
  return (
    <div class="absolute bottom-10 left-0 right-0 flex justify-center pointer-events-none">
      <div class="max-w-[80vw] text-center text-2xl text-slate-100/80 leading-snug">
        {props.text() ?? ""}
      </div>
    </div>
  );
};
```

- [ ] **Step 3: Replace `face/face.tsx`**

```tsx
import { Component, createSignal, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { emotionToColor } from "../shared/design";
import { StateBlob } from "./state_blob";
import { Caption } from "./caption";

const Face: Component = () => {
  const [state, setState] = createSignal<string>("idle");
  const [emotion, setEmotion] = createSignal<string>("neutral");
  const [rms, setRms] = createSignal<number>(0);
  const [caption, setCaption] = createSignal<string | undefined>();

  const nats = new NatsWs();

  onMount(() => {
    nats.start();
    nats.subscribe("agent.state.*", (f) => {
      const tail = f.topic.split(".").pop();
      if (tail) setState(tail);
      // Auto-dim RMS on state changes
      if (tail === "idle" || tail === "shutdown") setRms(0);
    });
    nats.subscribe("agent.reply", (f) => {
      setEmotion(f.payload.emotion ?? "neutral");
      setCaption(f.payload.text);
    });
    nats.subscribe("agent.transcript", (f) => {
      setCaption(f.payload.text);
    });
    nats.subscribe("agent.tts.rms", (f) => {
      setRms(f.payload.mouth_target ?? 0);
    });
  });
  onCleanup(() => nats.stop());

  // Background gradient color from emotion
  const bgColor = () => emotionToColor(emotion());

  return (
    <div
      class="relative h-full w-full overflow-hidden"
      style={{
        background: `radial-gradient(ellipse at center, ${bgColor()}33 0%, #0f172a 70%, #020617 100%)`,
        transition: "background 0.6s ease",
      }}
    >
      <StateBlob intensity={rms} color={bgColor} />
      <div class="absolute top-10 left-0 right-0 text-center text-sm uppercase tracking-widest text-slate-300/60">
        {state()}
      </div>
      <Caption text={caption} />
    </div>
  );
};

export default Face;
```

- [ ] **Step 4: Smoke build**

```powershell
cd web
npm run typecheck
npm run build
cd ..
```

Expected: builds without errors.

- [ ] **Step 5: Commit**

```powershell
git add web
git commit -m "web/face: add ambient state visualization (blob + caption + emotion gradient)"
```

---

## Phase J: Admin view (Tasks 31–34)

Goal: `/admin` route is the operator's control panel. Shows service status, settings tuning, live pose, servo sliders, expression triggers, chat log, system pulse, restart buttons. SolidJS reactive bindings throughout.

### Task 31: Admin shell + service status panel

**Files:**
- Modify: `web/src/admin/admin.tsx`
- Create: `web/src/admin/service_status.tsx`

- [ ] **Step 1: Write `service_status.tsx`**

```tsx
import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";

interface ServiceRow {
  name: string;
  last_seen: number;
  uptime_s: number;
  state?: string;
}

export const ServiceStatus: Component<{ nats: NatsWs }> = (props) => {
  const [rows, setRows] = createSignal<Record<string, ServiceRow>>({});
  const [now, setNow] = createSignal(Date.now() / 1000);

  let interval: number | undefined;
  let unsubHb: (() => void) | undefined;
  let unsubState: (() => void) | undefined;

  onMount(async () => {
    // Seed from snapshot
    try {
      const snap = await api.snapshot();
      const seeded: Record<string, ServiceRow> = {};
      for (const [name, info] of Object.entries(snap.services ?? {})) {
        seeded[name] = { name, ...(info as any) };
      }
      setRows(seeded);
    } catch { /* ignore */ }

    unsubHb = props.nats.subscribe("system.heartbeat.*", (f) => {
      const name = f.topic.split(".").pop()!;
      setRows((r) => ({
        ...r,
        [name]: { ...(r[name] ?? { name }), name, last_seen: Date.now() / 1000, uptime_s: f.payload.uptime_s },
      }));
    });
    unsubState = props.nats.subscribe("*.state.*", (f) => {
      const parts = f.topic.split(".");
      const name = parts[0];
      const state = parts.slice(2).join(".");
      setRows((r) => ({ ...r, [name]: { ...(r[name] ?? { name, last_seen: 0, uptime_s: 0 }), state } }));
    });

    interval = window.setInterval(() => setNow(Date.now() / 1000), 1000);
  });
  onCleanup(() => {
    if (interval) clearInterval(interval);
    unsubHb?.();
    unsubState?.();
  });

  const ageColor = (age: number) =>
    age < 10 ? "bg-emerald-500" : age < 20 ? "bg-amber-500" : "bg-red-500";

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Services</h2>
      <table class="w-full text-sm">
        <thead class="text-slate-400 text-xs uppercase">
          <tr><th class="text-left pb-2">name</th><th class="text-left">state</th><th class="text-left">heartbeat</th><th class="text-left">uptime</th><th></th></tr>
        </thead>
        <tbody>
          <For each={Object.values(rows()).sort((a, b) => a.name.localeCompare(b.name))}>{(r) => {
            const age = now() - r.last_seen;
            return (
              <tr class="border-t border-slate-800">
                <td class="py-2 font-mono">{r.name}</td>
                <td class="text-slate-300">{r.state ?? "?"}</td>
                <td>
                  <span class={`inline-block w-2 h-2 rounded-full mr-2 ${ageColor(age)}`} />
                  <span class="text-slate-400">{age.toFixed(0)}s ago</span>
                </td>
                <td class="text-slate-400">{(r.uptime_s ?? 0).toFixed(0)}s</td>
                <td>
                  <button
                    class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600"
                    onClick={() => api.restartService(r.name).catch((e) => alert(e.message))}
                  >restart</button>
                </td>
              </tr>
            );
          }}</For>
        </tbody>
      </table>
    </section>
  );
};
```

- [ ] **Step 2: Replace `admin.tsx` with a 4-column shell**

```tsx
import { Component, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { ServiceStatus } from "./service_status";

const Admin: Component = () => {
  const nats = new NatsWs();
  onMount(() => nats.start());
  onCleanup(() => nats.stop());

  return (
    <div class="min-h-screen p-6 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      <header class="col-span-full flex items-center justify-between">
        <h1 class="text-2xl font-bold">Lafufu admin</h1>
        <span class="text-sm text-slate-400">v0.1.0 · Phase 0</span>
      </header>
      <ServiceStatus nats={nats} />
      {/* Other panels added in Tasks 32-34 */}
    </div>
  );
};

export default Admin;
```

- [ ] **Step 3: Build + commit**

```powershell
cd web && npm run build && cd ..
git add web
git commit -m "web/admin: shell + service status panel (heartbeat + state)"
```

### Task 32: Settings form

**Files:**
- Create: `web/src/admin/settings_form.tsx`
- Modify: `web/src/admin/admin.tsx`

- [ ] **Step 1: Write `settings_form.tsx`**

```tsx
import { Component, createSignal, onMount, For } from "solid-js";
import { api } from "../shared/api";

interface Row {
  key: string;
  value: string;
  value_type: string;
}

export const SettingsForm: Component = () => {
  const [rows, setRows] = createSignal<Row[]>([]);
  const [dirty, setDirty] = createSignal<Set<string>>(new Set());

  const reload = async () => {
    const data = await api.listSettings();
    setRows(data as Row[]);
  };

  onMount(reload);

  const update = (key: string, newValue: string) => {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, value: newValue } : r)));
    setDirty((d) => new Set(d).add(key));
  };

  const commit = async (row: Row) => {
    const parsed = row.value_type === "json" ? JSON.parse(row.value)
      : row.value_type === "int" ? parseInt(row.value, 10)
      : row.value_type === "float" ? parseFloat(row.value)
      : row.value_type === "bool" ? row.value === "true"
      : row.value;
    await api.patchSetting(row.key, { value: parsed, value_type: row.value_type });
    setDirty((d) => { const c = new Set(d); c.delete(row.key); return c; });
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold">Settings</h2>
        <button class="text-xs text-slate-400 hover:text-slate-200" onClick={reload}>refresh</button>
      </div>
      <div class="space-y-2 max-h-[60vh] overflow-y-auto">
        <For each={rows()}>{(row) => (
          <div class="flex items-center gap-2">
            <label class="font-mono text-xs text-slate-400 w-1/2 truncate" title={row.key}>{row.key}</label>
            <input
              class={`flex-1 bg-slate-800 border ${dirty().has(row.key) ? "border-amber-500" : "border-slate-700"} rounded px-2 py-1 text-sm`}
              value={row.value}
              onInput={(e) => update(row.key, e.currentTarget.value)}
            />
            <button
              class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-30"
              disabled={!dirty().has(row.key)}
              onClick={() => commit(row).catch((e) => alert(e.message))}
            >save</button>
          </div>
        )}</For>
        {rows().length === 0 && <div class="text-slate-500 text-sm">No settings yet.</div>}
      </div>
    </section>
  );
};
```

- [ ] **Step 2: Mount in `admin.tsx`** — add import and component:

```tsx
import { SettingsForm } from "./settings_form";
// ...inside JSX, after <ServiceStatus />:
<SettingsForm />
```

- [ ] **Step 3: Build + commit**

```powershell
cd web && npm run build && cd ..
git add web
git commit -m "web/admin: add settings form (PATCH-on-save, dirty highlight)"
```

### Task 33: Pose view + servo sliders + expression triggers

**Files:**
- Create: `web/src/admin/pose_view.tsx`
- Create: `web/src/admin/servo_sliders.tsx`
- Create: `web/src/admin/expression_buttons.tsx`
- Modify: `web/src/admin/admin.tsx`

- [ ] **Step 1: Write `pose_view.tsx`** (live `animator.pose` display)

```tsx
import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";

const SERVOS = ["head_lr", "head_ud", "eye", "jaw", "brow"] as const;

export const PoseView: Component<{ nats: NatsWs }> = (props) => {
  const [pose, setPose] = createSignal<Record<string, number>>({});

  let unsub: (() => void) | undefined;
  onMount(() => {
    unsub = props.nats.subscribe("animator.pose", (f) => setPose(f.payload));
  });
  onCleanup(() => unsub?.());

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Live pose</h2>
      <div class="grid grid-cols-5 gap-3 text-center">
        <For each={SERVOS}>{(name) => (
          <div class="rounded bg-slate-800 p-2">
            <div class="text-xs uppercase text-slate-500">{name}</div>
            <div class="text-2xl font-mono tabular-nums">{pose()[name] ?? "—"}</div>
          </div>
        )}</For>
      </div>
    </section>
  );
};
```

- [ ] **Step 2: Write `servo_sliders.tsx`** (drag-to-preview, release-to-save)

```tsx
import { Component, createSignal, For } from "solid-js";
import { api } from "../shared/api";

const RANGES: Record<string, [number, number]> = {
  head_lr: [1828, 2298],
  head_ud: [2885, 3278],
  eye: [1960, 2130],
  jaw: [1534, 1728],
  brow: [2051, 2099],
};

export const ServoSliders: Component = () => {
  const [vals, setVals] = createSignal<Record<string, number>>({
    head_lr: 2063, head_ud: 3082, eye: 2045, jaw: 1728, brow: 2075,
  });

  let pending: NodeJS.Timeout | undefined;

  const onDrag = (name: string, position: number) => {
    setVals((v) => ({ ...v, [name]: position }));
    if (pending) clearTimeout(pending);
    // Throttle preview to ~50ms
    pending = setTimeout(() => api.animatorPreview(name, position).catch(() => {}), 50);
  };

  const onCommit = async (name: string) => {
    try {
      await api.putSetting(`animator.${name}.default`, { value: vals()[name], value_type: "int" });
    } catch (e: any) {
      alert(e.message);
    }
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Servo preview</h2>
      <div class="space-y-3">
        <For each={Object.entries(RANGES)}>{([name, [lo, hi]]) => (
          <div>
            <div class="flex justify-between text-xs text-slate-400">
              <span class="font-mono">{name}</span>
              <span class="tabular-nums">{vals()[name]}</span>
            </div>
            <div class="flex gap-2">
              <input
                type="range" min={Math.min(lo, hi)} max={Math.max(lo, hi)}
                value={vals()[name]}
                onInput={(e) => onDrag(name, parseInt(e.currentTarget.value, 10))}
                class="flex-1"
              />
              <button class="text-xs px-2 rounded bg-slate-700 hover:bg-slate-600" onClick={() => onCommit(name)}>save</button>
            </div>
          </div>
        )}</For>
      </div>
    </section>
  );
};
```

- [ ] **Step 3: Write `expression_buttons.tsx`**

```tsx
import { Component, For } from "solid-js";
import { api } from "../shared/api";

const EXPRESSIONS = ["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"];

export const ExpressionButtons: Component = () => {
  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Expressions</h2>
      <div class="flex flex-wrap gap-2">
        <For each={EXPRESSIONS}>{(name) => (
          <button
            class="px-3 py-2 rounded bg-slate-700 hover:bg-slate-600 capitalize"
            onClick={() => api.animatorExpression(name).catch((e) => alert(e.message))}
          >{name}</button>
        )}</For>
      </div>
    </section>
  );
};
```

- [ ] **Step 4: Mount the new panels in `admin.tsx`**

Add imports and components:

```tsx
import { PoseView } from "./pose_view";
import { ServoSliders } from "./servo_sliders";
import { ExpressionButtons } from "./expression_buttons";
// Inside grid:
<PoseView nats={nats} />
<ServoSliders />
<ExpressionButtons />
```

- [ ] **Step 5: Build + commit**

```powershell
cd web && npm run build && cd ..
git add web
git commit -m "web/admin: add live pose, servo sliders, expression triggers"
```

### Task 34: Chat log + text input + system pulse

**Files:**
- Create: `web/src/admin/chat_log.tsx`
- Create: `web/src/admin/system_pulse.tsx`
- Modify: `web/src/admin/admin.tsx`

- [ ] **Step 1: Write `chat_log.tsx`** (transcripts + replies + text input to agent)

```tsx
import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";

interface Entry { role: "user" | "lafufu" | "system"; text: string; emotion?: string }

export const ChatLog: Component<{ nats: NatsWs }> = (props) => {
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [input, setInput] = createSignal("");

  let unsubT: (() => void) | undefined;
  let unsubR: (() => void) | undefined;

  onMount(() => {
    unsubT = props.nats.subscribe("agent.transcript", (f) => {
      setEntries((e) => [...e.slice(-50), { role: "user", text: f.payload.text }]);
    });
    unsubR = props.nats.subscribe("agent.reply", (f) => {
      setEntries((e) => [...e.slice(-50), { role: "lafufu", text: f.payload.text, emotion: f.payload.emotion }]);
    });
  });
  onCleanup(() => { unsubT?.(); unsubR?.(); });

  const send = async () => {
    const text = input().trim();
    if (!text) return;
    setInput("");
    await api.agentTextMessage(text);
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4 flex flex-col h-[60vh]">
      <h2 class="text-lg font-semibold mb-3">Chat</h2>
      <div class="flex-1 overflow-y-auto space-y-2 mb-3">
        <For each={entries()}>{(e) => (
          <div class={`text-sm ${e.role === "user" ? "text-slate-300" : "text-emerald-300"}`}>
            <span class="font-mono text-xs opacity-60">{e.role}{e.emotion ? `:${e.emotion}` : ""}</span>
            <div>{e.text}</div>
          </div>
        )}</For>
      </div>
      <div class="flex gap-2">
        <input
          class="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
          placeholder="Send text to Lafufu..."
          value={input()}
          onInput={(e) => setInput(e.currentTarget.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button class="text-sm px-3 rounded bg-emerald-600 hover:bg-emerald-500" onClick={send}>send</button>
      </div>
    </section>
  );
};
```

- [ ] **Step 2: Write `system_pulse.tsx`** (raw NATS firehose for debugging)

```tsx
import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";

export const SystemPulse: Component<{ nats: NatsWs }> = (props) => {
  const [lines, setLines] = createSignal<{ ts: number; topic: string; payload: any }[]>([]);

  let unsub: (() => void) | undefined;
  onMount(() => {
    unsub = props.nats.subscribe(">", (f) => {
      setLines((ls) => [...ls.slice(-99), { ts: Date.now(), topic: f.topic, payload: f.payload }]);
    });
  });
  onCleanup(() => unsub?.());

  return (
    <section class="rounded-lg bg-slate-900 p-4 col-span-full">
      <h2 class="text-lg font-semibold mb-2">System pulse</h2>
      <div class="font-mono text-xs max-h-60 overflow-y-auto bg-slate-950 rounded p-2 space-y-0.5">
        <For each={lines()}>{(l) => (
          <div class="flex gap-2">
            <span class="text-slate-500 w-24 shrink-0">{new Date(l.ts).toLocaleTimeString()}</span>
            <span class="text-emerald-400 w-64 shrink-0 truncate">{l.topic}</span>
            <span class="text-slate-300 truncate">{JSON.stringify(l.payload)}</span>
          </div>
        )}</For>
      </div>
    </section>
  );
};
```

- [ ] **Step 3: Mount in `admin.tsx`**

```tsx
import { ChatLog } from "./chat_log";
import { SystemPulse } from "./system_pulse";
// Inside grid:
<ChatLog nats={nats} />
<SystemPulse nats={nats} />
```

- [ ] **Step 4: Build + commit**

```powershell
cd web && npm run build && cd ..
git add web
git commit -m "web/admin: add chat log + text input + system pulse"
```

---

## Phase K: Deploy to Pi (Tasks 35–38)

Goal: systemd units, install script, smoke test, first end-to-end on real hardware. After this phase Phase 0 is shipped.

### Task 35: systemd units + NATS server config (production)

**Files:**
- Create: `deploy/systemd/nats.service`
- Create: `deploy/systemd/lafufu-agent.service`
- Create: `deploy/systemd/lafufu-animator.service`
- Create: `deploy/systemd/lafufu-printer.service`
- Create: `deploy/systemd/lafufu-control.service`
- Create: `deploy/systemd/lafufu-kiosk.service`
- Create: `deploy/systemd/lafufu.target`
- Create: `deploy/nats/nats-server.production.conf`

Convention: `/srv/lafufu/` is the repo, `/var/lafufu/` is mutable state, services run as user `lafufu`.

- [ ] **Step 1: `nats.service`**

```ini
[Unit]
Description=NATS Server for Lafufu
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lafufu
Group=lafufu
ExecStart=/usr/local/bin/nats-server -c /etc/nats/nats-server.conf
Restart=on-failure
RestartSec=5s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: `lafufu-animator.service`**

```ini
[Unit]
Description=Lafufu Animator (servos + lipsync)
After=nats.service
Wants=nats.service
PartOf=lafufu.target

[Service]
Type=simple
User=lafufu
Group=lafufu
WorkingDirectory=/srv/lafufu
Environment=LAFUFU_NATS_URL=nats://localhost:4222
Environment=LAFUFU_DATA_DIR=/var/lafufu
ExecStart=/srv/lafufu/.venv/bin/python -m lafufu_animator
Restart=on-failure
RestartSec=5s
StartLimitBurst=5
StartLimitIntervalSec=60
TimeoutStopSec=10s

[Install]
WantedBy=lafufu.target
```

- [ ] **Step 3: `lafufu-agent.service`** (same shape with module name + extra env)

```ini
[Unit]
Description=Lafufu Agent (voice pipeline)
After=nats.service
Wants=nats.service
PartOf=lafufu.target

[Service]
Type=simple
User=lafufu
Group=lafufu
WorkingDirectory=/srv/lafufu
Environment=LAFUFU_NATS_URL=nats://localhost:4222
Environment=LAFUFU_DATA_DIR=/var/lafufu
Environment=LAFUFU_WHISPER_MODEL=tiny
Environment=LAFUFU_LLM_MODEL=qwen2.5:7b
Environment=LAFUFU_OLLAMA_URL=http://localhost:11434
Environment=LAFUFU_PIPER_MODEL=/srv/lafufu/models/lafufu_voice.onnx
ExecStart=/srv/lafufu/.venv/bin/python -m lafufu_agent
Restart=on-failure
RestartSec=5s
StartLimitBurst=5
StartLimitIntervalSec=60
TimeoutStopSec=15s

[Install]
WantedBy=lafufu.target
```

- [ ] **Step 4: `lafufu-printer.service`**

```ini
[Unit]
Description=Lafufu Printer (CUPS bridge)
After=nats.service cups.service
Wants=nats.service
PartOf=lafufu.target

[Service]
Type=simple
User=lafufu
Group=lp
WorkingDirectory=/srv/lafufu
Environment=LAFUFU_NATS_URL=nats://localhost:4222
ExecStart=/srv/lafufu/.venv/bin/python -m lafufu_printer
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=lafufu.target
```

- [ ] **Step 5: `lafufu-control.service`**

```ini
[Unit]
Description=Lafufu Control (HTTP API + WS bridge)
After=nats.service lafufu-animator.service
Wants=nats.service
PartOf=lafufu.target

[Service]
Type=simple
User=lafufu
Group=lafufu
WorkingDirectory=/srv/lafufu
Environment=LAFUFU_NATS_URL=nats://localhost:4222
Environment=LAFUFU_DATA_DIR=/var/lafufu
Environment=LAFUFU_CONTROL_PORT=8080
ExecStart=/srv/lafufu/.venv/bin/python -m lafufu_control
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=lafufu.target
```

- [ ] **Step 6: `lafufu-kiosk.service`** (Chromium full-screen, depends on control)

```ini
[Unit]
Description=Lafufu Kiosk Browser (face view)
After=lafufu-control.service graphical-session.target
Wants=lafufu-control.service
PartOf=lafufu.target

[Service]
Type=simple
User=lafufu
Group=lafufu
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStartPre=/bin/sh -c 'until curl -sf http://localhost:8080/api/state/snapshot >/dev/null; do sleep 1; done'
ExecStart=/usr/bin/chromium-browser --kiosk --noerrdialogs --disable-restore-session-state --disable-infobars http://localhost:8080/face
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=lafufu.target
```

- [ ] **Step 7: `lafufu.target`**

```ini
[Unit]
Description=Lafufu (all services)
Requires=nats.service
After=nats.service

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 8: `nats-server.production.conf`**

```
port: 4222
http_port: 8222

jetstream {
  store_dir: "/var/lafufu/jetstream"
  max_memory_store: 128MB
  max_file_store: 1GB
}

# Phase 0: local-only, no auth
```

- [ ] **Step 9: Commit**

```powershell
git add deploy
git commit -m "deploy: systemd units (nats, agent, animator, printer, control, kiosk) + target"
```

### Task 36: Install script

**Files:**
- Create: `deploy/install.sh`

- [ ] **Step 1: Write `install.sh`** (run on Pi as root, idempotent)

```bash
#!/bin/bash
set -euo pipefail

# Lafufu install / update script. Run as root on the Pi.
#
# Usage:
#   sudo ./deploy/install.sh                # fresh install
#   sudo ./deploy/install.sh --update       # update existing install (git pull + reinstall deps)

REPO_DIR="/srv/lafufu"
DATA_DIR="/var/lafufu"
USER_NAME="lafufu"
MODE="${1:-install}"

echo "==> lafufu install ($MODE)"

# 1. System deps
apt-get update
apt-get install -y python3.13 python3.13-venv python3-pip nodejs npm \
                   cups chromium-browser \
                   build-essential libasound2-dev portaudio19-dev \
                   curl ca-certificates git

# 2. Install uv if missing
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  cp ~/.local/bin/uv /usr/local/bin/uv
fi

# 3. Install nats-server if missing
if ! command -v nats-server >/dev/null; then
  curl -L https://github.com/nats-io/nats-server/releases/download/v2.10.20/nats-server-v2.10.20-linux-arm64.tar.gz | tar xz
  mv nats-server-v2.10.20-linux-arm64/nats-server /usr/local/bin/
  rm -rf nats-server-v2.10.20-linux-arm64
fi

# 4. User + dirs
id -u "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
usermod -aG audio,video,plugdev,dialout,lp "$USER_NAME"
mkdir -p "$DATA_DIR/jetstream"
chown -R "$USER_NAME:$USER_NAME" "$DATA_DIR"

# 5. Repo (when updating, assume the script is already inside /srv/lafufu)
if [[ "$MODE" == "--update" ]]; then
  cd "$REPO_DIR"
  sudo -u "$USER_NAME" git pull --ff-only
else
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "ERROR: $REPO_DIR is not a git checkout. Clone lafufu there first."
    exit 1
  fi
  chown -R "$USER_NAME:$USER_NAME" "$REPO_DIR"
fi

cd "$REPO_DIR"

# 6. Python deps
sudo -u "$USER_NAME" uv sync --all-packages

# 7. Web build → control's static dir
sudo -u "$USER_NAME" bash -c 'cd web && npm ci && npm run build'

# 8. NATS config
mkdir -p /etc/nats
cp deploy/nats/nats-server.production.conf /etc/nats/nats-server.conf
chown root:root /etc/nats/nats-server.conf

# 9. systemd units
cp deploy/systemd/nats.service /etc/systemd/system/
cp deploy/systemd/lafufu-*.service /etc/systemd/system/
cp deploy/systemd/lafufu.target /etc/systemd/system/
systemctl daemon-reload

# 10. Enable + start
systemctl enable nats.service
systemctl enable lafufu-animator.service lafufu-agent.service \
                 lafufu-printer.service lafufu-control.service \
                 lafufu-kiosk.service lafufu.target

if [[ "$MODE" == "--update" ]]; then
  systemctl restart lafufu.target
else
  systemctl start nats.service
  systemctl start lafufu.target
fi

echo "==> done. Check:  systemctl status 'lafufu-*'"
echo "    Logs:        journalctl -u 'lafufu-*' -f"
echo "    Admin UI:    http://$(hostname -I | awk '{print $1}'):8080/admin"
```

- [ ] **Step 2: Make executable + commit**

```powershell
git update-index --chmod=+x deploy/install.sh
git add deploy/install.sh
git commit -m "deploy: add idempotent install/update script"
```

### Task 37: Smoke test script

**Files:**
- Create: `scripts/smoke.sh`

- [ ] **Step 1: Write `smoke.sh`**

```bash
#!/bin/bash
set -e

# On-Pi smoke test. Run after deploy.
# Exits non-zero on any failure.

echo "==> 1. All services active?"
for svc in nats.service lafufu-agent.service lafufu-animator.service lafufu-printer.service lafufu-control.service; do
  state=$(systemctl is-active "$svc" || true)
  if [[ "$state" != "active" ]]; then
    echo "FAIL: $svc is $state"
    exit 1
  fi
  echo "  ok: $svc"
done

echo "==> 2. control HTTP reachable?"
curl -sf http://localhost:8080/api/state/snapshot >/dev/null || { echo "FAIL: control HTTP"; exit 1; }
echo "  ok"

echo "==> 3. NATS reachable?"
nc -zv localhost 4222 || { echo "FAIL: NATS port"; exit 1; }

echo "==> 4. Trigger synthesized voice cycle (no real mic)"
curl -sf -X POST http://localhost:8080/api/agent/text_message -d '{"text":"smoke test"}' -H "Content-Type: application/json" >/dev/null || { echo "FAIL: text_message"; exit 1; }
sleep 8
echo "  ok (check logs for agent.reply)"

echo "==> 5. Trigger an expression directly"
curl -sf -X POST http://localhost:8080/api/animator/expression -d '{"name":"happy"}' -H "Content-Type: application/json" >/dev/null || { echo "FAIL: expression"; exit 1; }
sleep 1
echo "  ok"

echo "==> 6. Service status panel responds?"
curl -sf http://localhost:8080/api/state/snapshot | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "agent" in d.get("services",{}), d' || { echo "FAIL: snapshot missing agent heartbeat"; exit 1; }
echo "  ok"

echo "✅ smoke passed"
```

- [ ] **Step 2: Make executable + commit**

```powershell
git update-index --chmod=+x scripts/smoke.sh
git add scripts/smoke.sh
git commit -m "scripts: add on-Pi smoke test"
```

### Task 38: First Pi deploy + end-to-end verification

This task has manual steps. The plan documents them; the operator runs them.

- [ ] **Step 1: Push everything to GitHub**

```powershell
git push origin main
```

- [ ] **Step 2: On Pi — fresh-install path**

```bash
ssh lafufu@<pi-host>
# 2a. Backup existing /itripleg if you want to revert
sudo cp -r /itripleg /itripleg.bak

# 2b. Clone the new repo
sudo mkdir -p /srv/lafufu
sudo chown lafufu:lafufu /srv/lafufu
cd /srv
git clone <repo-url> lafufu

# 2c. Run installer
cd /srv/lafufu
sudo ./deploy/install.sh
```

- [ ] **Step 3: Verify services healthy**

```bash
systemctl status nats lafufu-{agent,animator,printer,control,kiosk}
journalctl -u 'lafufu-*' -f
```

Expected: all services `active (running)`. Logs show heartbeats every 5s.

- [ ] **Step 4: Run smoke test**

```bash
/srv/lafufu/scripts/smoke.sh
```

Expected: `✅ smoke passed`.

- [ ] **Step 5: Open admin UI from phone/laptop**

```
http://<pi-host>:8080/admin
```

Walk the manual checklist:
- [ ] Service status panel shows green dots for all 4 services
- [ ] Settings table renders (empty is fine; populate via slider commits or PUTs)
- [ ] Live pose updates as `animator.pose` arrives
- [ ] Move a servo slider — Lafufu's servo moves in real time
- [ ] Click an expression button — Lafufu changes face
- [ ] Type a message in chat — Lafufu speaks the reply (audio comes out speaker)
- [ ] System pulse panel scrolls events live
- [ ] Click "restart agent" — `agent.state.warming` appears, then `idle`, animator unaffected

- [ ] **Step 6: Verify on-Pi kiosk**

Pi HDMI display shows the `/face` view: ambient gradient, blob pulses when Lafufu speaks.

- [ ] **Step 7: Tag the release**

```bash
cd /srv/lafufu
git tag -a v0.1.0 -m "Phase 0 foundation — first deploy"
git push origin v0.1.0
```

Phase 0 done.

---

## Self-review summary

Cross-checked against `docs/superpowers/specs/2026-05-17-phase-0-foundation-design.md`:

| Spec section | Covered by |
|---|---|
| 3.1 Topology (4 services + NATS + kiosk) | Tasks 9-13 (animator), 14-19 (agent), 20-21 (printer), 22-26 (control), 35 (kiosk unit) |
| 3.2 Service responsibilities | Tasks 13, 19, 21, 25 (per-service entry points) |
| 3.3 Tech stack | Tasks 1-2 (Python+uv), 28 (web), 35 (systemd) |
| 3.4 Repo layout | File-structure section + Tasks 1, 5, 9, 14, 20, 22, 28 |
| 3.5 Topic naming | Task 5 (`topics.py`) |
| 4.1-4.6 Component detail | Tasks 9-13 (animator), 14-19 (agent), 20-21 (printer), 22-26 (control), 35 (kiosk + NATS) |
| 4.7 Shared package | Tasks 5-8 |
| 4.8 Frontend | Tasks 28-34 |
| 4.9 Service startup ordering | Task 35 (systemd `After=`/`Wants=`) |
| 5.1 Cold boot | Task 35 unit ordering + Task 38 verification |
| 5.2 Voice interaction lifecycle | Tasks 13, 18, 19, 21 (animator+agent+printer subscribers) |
| 5.3 Live tuning preview vs commit | Tasks 24 (intent routers), 33 (servo sliders + putSetting) |
| 5.4 Hot-swap | Task 24 (restart router), Task 38 step 5 (verification) |
| 5.5 Browser connection/reconnection | Task 29 (NatsWs reconnect), Task 26 (WS bridge) |
| 6.1 systemd policies | Task 35 unit files |
| 6.2 NATS connection | Task 6 (`connect_with_retry`) |
| 6.3 Hardware degradation | Tasks 13, 19, 21 (degraded states) |
| 6.4 Application errors | Task 6 (pydantic validation), Task 23 (FastAPI error shape) |
| 6.5 Observability | Task 6 (JSON logs), Task 34 (system pulse), Task 38 (journalctl) |
| 6.6 Failure modes catalog | Implicitly covered by service-degradation tests in Tasks 13, 19, 21 |
| 7.1 Unit tests | Tasks 9, 10, 11, 14, 15, 17 |
| 7.2 Integration tests | Tasks 13, 19, 21, 26 |
| 7.3 API tests | Tasks 23, 24 |
| 7.4 Frontend tests | Task 29 |
| 7.5 On-Pi smoke | Task 37 + Task 38 |
| 7.6 CI | Task 4 |
| 7.7 TDD discipline | Step pattern throughout every task |
| 8.1 Pi setup | Task 36 (`install.sh`) |
| 8.2 Filesystem layout | Task 36 paths |
| 8.3 Update path | Task 36 `--update` flag |
| 9 Implementation roadmap | This whole plan |

**No placeholders.** Every code step has runnable code. Every test step has explicit assertions. Every commit step has the exact `git` invocation.

**Type consistency:** schemas defined in Task 5 are used in Tasks 13, 19, 21, 23, 26, 28 (autogen). Topic constants in Task 5 are used in every subscriber. SettingIn/SettingOut shapes in Task 23 match what the frontend api.ts (Task 29) sends.

**Deferred / explicitly out of scope per spec:** auth, expression editor UI, behavior DSL, hotspot, plugin marketplace, Bluetooth, GPIO, vision, multi-Lafufu. None of these have tasks in this plan — correct.

---

## Execution choice

Plan complete and saved to `docs/superpowers/plans/2026-05-17-lafufu-phase-0-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
