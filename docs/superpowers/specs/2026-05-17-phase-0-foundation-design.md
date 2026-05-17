# Lafufu Phase 0 — Foundation Design

**Status:** Draft, awaiting review
**Date:** 2026-05-17
**Scope:** The architectural spine for the Lafufu rebuild — everything later phases plug into.

---

## 1. Context

The existing Lafufu (`C:\dev\lafufu-jb`, deployed at `/itripleg/merged-lafufu/` on a Pi 5) is a single ~2200-line `dynamixel.py` Python monolith. It runs Whisper STT, qwen2.5:7b LLM via Ollama, Piper TTS, drives 5 Dynamixel servos via a U2D2 serial bus, computes its own lipsync, plays background mp4 via mpv, and autostarts under labwc kiosk. It works but is hard to maintain, hard to extend, can't be tuned without an SSH session and a code edit, and has no API surface. Any single component crashing takes down the whole system.

The decision is to **rebuild from scratch** with production-grade architecture and clean composability. This Phase 0 spec defines the foundation. Later phases (1–4) layer features onto this spine.

---

## 2. Goals & Non-Goals

### Phase 0 goals

- **Composable, multi-process architecture** with single-purpose services
- **NATS-based event bus** as the canonical inter-service communication
- **Hot-swappable services** — restart any one without bringing down others
- **API-first** — every state and every action reachable via HTTP/WS, contracts defined before implementation
- **Web-configurable** — every tunable Lafufu setting changeable from a browser
- **Display-as-UI** — replace mpv background with a fullscreen Chromium kiosk pointed at the local web UI
- **Feature parity** with current Lafufu (voice loop, servo motion, expressions, lipsync, printer)
- **Clean repo, fresh codebase** in a new folder, separate from `lafufu-jb`
- **Testable end-to-end without hardware** via mocked dependencies

### Phase 0 non-goals (deferred)

- Live-preview expression editor with visual servo curve authoring → Phase 1
- Behavior composition DSL (rule-based triggers, scripted sequences) → Phase 2
- Pi-as-Wi-Fi-hotspot + spectator PWA → Phase 3
- Plugin marketplace UI → Phase 4
- Bluetooth integration → Phase 4
- GPIO service → Phase 4 (template exists in Phase 0)
- Computer vision / camera → Phase 4
- Multi-Lafufu coordination → never (single instance by design)
- Auth/RBAC → Phase 0 ships local-network-only access; auth in Phase 2 when remote admin matters
- Prometheus/Grafana metrics → Phase 4; Phase 0 uses the admin UI status panel as the dashboard

---

## 3. Architecture

### 3.1 Topology

```
┌──────────────────────────────────────────────────────────────────┐
│                              Pi 5                                │
│                                                                  │
│  ┌──────────┐                                                    │
│  │  NATS    │  ← :4222, JetStream enabled (small retention for    │
│  │  server  │     replay-on-reconnect on state.* topics)          │
│  └────┬─────┘                                                     │
│       │                                                           │
│   ┌───┴─────────┬──────────────┬───────────┬─────────────┐        │
│  ┌▼──────┐  ┌───▼───────┐  ┌───▼─────┐  ┌──▼────────┐             │
│  │ agent │  │ animator  │  │ printer │  │  control  │←─ SQLite    │
│  │       │  │           │  │         │  │           │             │
│  │ STT + │  │ servos +  │  │ thermal │  │ FastAPI + │             │
│  │ LLM + │  │ lipsync + │  │ printer │  │ WS bridge │             │
│  │ TTS   │  │ U2D2      │  │ via CUPS│  │ + SPA host│             │
│  └───────┘  └───────────┘  └─────────┘  └─────┬─────┘             │
│                                                │                   │
│                                          ┌─────▼──────────┐        │
│                                          │ chromium-kiosk │        │
│                                          │ /face route on │        │
│                                          │ HDMI display   │        │
│                                          └────────────────┘        │
└──────────────────────────────────────────────────────────────────┘
                                                ▲
                                                │ (same control,
                                                │  different route)
                                          ┌─────┴──────┐
                                          │ Phone /    │
                                          │ Laptop     │ → /admin
                                          └────────────┘
```

**Four services + NATS + Chromium kiosk.** Each service is one OS process, owns one domain, can be restarted independently. The on-Pi browser is just another WebSocket client of `control`, not a special case.

### 3.2 Service responsibilities (one-line each)

| Service | Owns |
|---|---|
| `agent` | Voice pipeline: mic → STT → LLM → TTS. Owns USB mic + speaker. |
| `animator` | Servo bus (U2D2), lipsync, expression/gesture playback. Only process touching servos. |
| `printer` | USB thermal printer via CUPS. Auto-prints replies + serves on-demand intents. |
| `control` | SQLite (single writer), HTTP REST API, WebSocket bridge, serves SPA, kiosk target. |
| NATS server | Topic routing + small JetStream retention for state replay. |
| `chromium-kiosk` | systemd-managed full-screen browser pointed at `localhost:8080/face`. Replaces mpv. |

### 3.3 Tech stack

| Layer | Pick | Rationale |
|---|---|---|
| Backend language | Python 3.13 | Matches Pi, Whisper/Piper/Ollama bindings native |
| Package manager | `uv` workspace | Fast, modern, monorepo-friendly |
| Event bus | NATS server + `nats-py` async client | ~10MB binary, zero-ops, perfect topology fit |
| HTTP API | FastAPI + uvicorn (uvloop) | Async-native, auto-OpenAPI, mature |
| Database | SQLite + SQLModel | Zero-ops, ACID, no extra service |
| Frontend language | TypeScript | Type safety end-to-end with backend schemas |
| Frontend framework | **SolidJS** (recommended; final pick during Phase 0 implementation) | Fine-grained reactivity is ideal for live-streaming UI; small bundle |
| Frontend build | Vite + TailwindCSS | Standard, fast, well-supported |
| Supervisor | systemd | Standard, declarative restart policies |
| Kiosk | Chromium in `--kiosk` mode | Replaces mpv background |
| Schema sync | pydantic → JSON Schema → TypeScript via `datamodel-code-generator` | Single source of truth, no manual type duplication |

### 3.4 Repo layout

```
lafufu/                              ← NEW folder (not lafufu-jb), git-fresh
├── packages/
│   ├── shared/                      Topic constants, pydantic schemas,
│   │                                NATS helper, BaseService template
│   ├── agent/                       Voice pipeline service
│   ├── animator/                    Servo + lipsync service
│   ├── printer/                     CUPS print service
│   └── control/                     API + DB + WS bridge + SPA host
├── web/                             TypeScript SPA (SolidJS + Vite)
│   ├── src/
│   │   ├── shared/                  Design system, NATS-over-WS client,
│   │   │                            auto-generated types from pydantic
│   │   ├── face/                    /face route (ambient on-Pi view)
│   │   ├── admin/                   /admin route (control panel)
│   │   ├── app.tsx                  Router
│   │   └── main.tsx
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── package.json
├── deploy/
│   ├── systemd/                     lafufu-*.service unit files + target
│   ├── nats/                        nats-server.conf
│   └── install.sh                   Pi install/update script
├── scripts/
│   └── smoke.sh                     On-Pi smoke test
├── docs/
│   └── superpowers/specs/           This spec + future specs
├── pyproject.toml                   uv workspace root
└── README.md
```

### 3.5 Topic naming convention

All topic strings live in `packages/shared/topics.py` — services reference constants, never literals. Hierarchical, wildcard-friendly.

```python
# State changes (JetStream replay on reconnect)
AGENT_STATE = "agent.state"             # .{warming|idle|listening|thinking|speaking|degraded|shutdown}
ANIMATOR_STATE = "animator.state"       # .{idle|active|degraded}
PRINTER_STATE = "printer.state"         # .{idle|printing|error|offline}
SYSTEM_SERVICE = "system.service"       # .{starting|ready|restarting|stopped}

# Content events (fire-and-forget, no replay)
AGENT_TRANSCRIPT = "agent.transcript"
AGENT_REPLY = "agent.reply"             # {text, emotion}
AGENT_TTS_RMS = "agent.tts.rms"         # high-freq lipsync driver

ANIMATOR_POSE = "animator.pose"         # ~20Hz current positions
ANIMATOR_INTENT = "animator.intent"     # .{set_pose|preview|play_expression|gesture}
ANIMATOR_EVENT = "animator.event"       # .{gesture_done|lipsync_start|lipsync_end}

PRINTER_INTENT = "printer.intent"       # .{print_text|print_transcript|test_page}
PRINTER_EVENT = "printer.event"         # .{job_started|job_done|paper_out|jam}

# Config + system
CONFIG_CHANGED = "config.changed"       # .<dotted_key>
SYSTEM_HEARTBEAT = "system.heartbeat"   # .<service_name> (every 5s)
SYSTEM_ERROR = "system.error"           # .<service_name>.<error_kind>
```

Wildcard examples: `agent.>` (all agent topics), `>.state.>` (all state transitions), `system.heartbeat.>` (all heartbeats).

---

## 4. Components (detailed)

### 4.1 `agent` — voice pipeline

- **Subscribes:** `animator.event.gesture_done`, `config.changed.agent.>`, `agent.intent.text_message` (for headless input)
- **Publishes:** `agent.state.*`, `agent.transcript`, `agent.reply`, `agent.tts.rms`
- **Internal files:** `audio_capture.py`, `vad.py`, `stt.py`, `llm.py`, `tts.py`, `pipeline.py`, `main.py`
- **External deps:** `pyaudio`, `openai-whisper`, `httpx` (Ollama client), `piper-tts`, `nats-py`
- **Hardware:** USB mic (e.g., Shure MV88), USB speaker (e.g., Jabra SPEAK 510)
- **Startup:** Connect NATS → publish `agent.state.warming` → load Whisper tiny → warm Ollama → publish `agent.state.idle` → enter mic loop
- **Hot-swap:** `systemctl restart lafufu-agent` — animator/printer/control unaffected, browsers reconnect WS, ~5s blackout
- **Degraded modes:** no mic → accept text input only; Ollama down → retry with backoff, surface in admin UI; no speaker → continue with print/log only

### 4.2 `animator` — motion + lipsync

- **Subscribes:** `animator.intent`, `agent.tts.rms`, `agent.reply` (to set expression from emotion), `config.changed.animator.>`
- **Publishes:** `animator.pose` (~20Hz), `animator.event.*`, `animator.state.*`
- **Internal files:** `dxl_bus.py`, `expressions.py`, `lipsync.py`, `pose.py`, `main.py`
- **External deps:** `dynamixel-sdk`, `nats-py`, `numpy` (for envelope math)
- **Hardware:** U2D2 + 5 Dynamixel servos
- **Startup:** Connect NATS → auto-detect U2D2 → enable torque → idle pose → publish `animator.state.idle` (or `.degraded` if no U2D2)
- **Hot-swap:** Restart unit; SIGTERM handler disables torque cleanly. Brief 1-2s servo freeze.
- **Degraded mode:** no U2D2 → accept intents, log them, publish synthetic `animator.pose` so UI still has something to draw

### 4.3 `printer` — thermal printer

- **Subscribes:** `agent.reply` (auto-print if enabled), `printer.intent`, `config.changed.printer.>`
- **Publishes:** `printer.state.*`, `printer.event.*`
- **Internal files:** `cups_client.py`, `formatter.py`, `main.py`
- **External deps:** `pycups` (or shell out to `lp`), `nats-py`
- **Hardware:** USB thermal printer (Memory Corp. 09c5:0588)
- **Startup:** Connect NATS → query CUPS for printers → publish state
- **Hot-swap:** Trivial restart. Crashing or being absent never affects other services.
- **Degraded mode:** no printer → publish `printer.state.offline`, noop on print requests

### 4.4 `control` — API + DB + UI + bridge

- **Subscribes:** `>` conceptually (everything is fan-outable). In practice the WS bridge subscribes **lazily** — only to topics at least one connected browser has requested. High-freq topics like `animator.pose` and `agent.tts.rms` aren't decoded unless a browser cares.
- **Publishes:** `animator.intent.*` (when admin user triggers something), `agent.intent.*`, `config.changed.*`, `system.service.*`
- **Internal files:** `db.py`, `models/` (Settings, Expressions, Behaviors-stubs, Plugins-stubs), `api/` (FastAPI routers), `ws_bridge.py`, `static/` (built SPA), `main.py`
- **External deps:** `fastapi`, `uvicorn[standard]`, `sqlmodel`, `nats-py`, `pydantic`
- **Hardware:** None
- **Startup:** Connect NATS → open DB (auto-migrate) → mount API routes → start WS bridge → bind `0.0.0.0:8080`
- **Hot-swap:** Restart unit; browsers lose WS for ~2s and auto-reconnect with JetStream backfill. Voice loop unaffected.

### 4.5 NATS server

- **Config:** Port 4222, JetStream enabled with file-backed retention on `agent.state.>`, `animator.state.>`, `printer.state.>`, `system.service.>` (last ~30s). High-frequency topics (`animator.pose`, `agent.tts.rms`) are core-NATS only (no replay).
- **Startup:** Standalone systemd unit, runs before all `lafufu-*` services.

### 4.6 `chromium-kiosk`

- **What:** systemd user-service running `chromium-browser --kiosk --noerrdialogs --disable-restore-session-state http://localhost:8080/face`
- **Depends on:** `control.service` (waits for `:8080` to respond)
- **Replaces:** Current mpv + 559MB mp4 setup

### 4.7 `packages/shared/`

```
shared/
├── topics.py          All topic name constants
├── schemas.py         Pydantic models for every event payload (single source of truth)
├── nats_helper.py     connect_with_retry(), typed publish/subscribe, heartbeat task
├── base_service.py    BaseService class: standard lifecycle, signal handling, state machine
└── settings.py        Env var loader, paths
```

The `BaseService` template (subclassed by every Python service) provides:
- NATS connect-with-retry
- Heartbeat task (publishes `system.heartbeat.<name>` every 5s)
- State machine helpers (publish `<service>.state.X` transitions)
- Graceful SIGTERM/SIGINT handling with custom shutdown hook
- Structured JSON logging to stdout (captured by journald)

This is the seam that makes adding future services (GPIO, sensors, vision) a copy-paste of `printer`.

### 4.8 `web/` frontend

Single SolidJS+Vite SPA serving two route trees:
- `/face` — ambient on-Pi view, optimized for always-on 1080p display, low CPU animations
- `/admin` — control panel, optimized for phone/laptop interaction

Shared design system, shared NATS-over-WS client, shared types auto-generated from pydantic schemas via `datamodel-code-generator` during the build.

### 4.9 Service startup ordering

systemd `After=` / `Wants=` chain:

```
network-online.target
   ↓
nats.service
   ├─► animator.service
   ├─► agent.service
   ├─► printer.service
   └─► control.service       (waits for animator so DB↔pose seed is clean)
            ↓
   chromium-kiosk.service    (waits for control)
```

A `lafufu.target` unit groups `agent`, `animator`, `printer`, `control` for system-wide `systemctl stop lafufu.target`.

---

## 5. Data flow

### 5.1 Cold boot sequence

1. Pi powers on, `network-online.target` reached
2. `nats.service` starts → bus up on :4222
3. `animator`, `agent`, `printer` start in parallel:
   - Each calls `connect_with_retry()`
   - `animator` auto-detects U2D2, enables torque, moves to idle, publishes `animator.state.idle`
   - `agent` publishes `agent.state.warming` → loads Whisper (~5s), warms Ollama (~3s hot, 30-60s cold — admin UI binds the warming state to a banner), then publishes `agent.state.idle`
   - `printer` queries CUPS, publishes `printer.state.idle` or `.offline`
4. `control` starts (waits for `animator`): opens DB, mounts routes, starts WS bridge, binds :8080
5. `chromium-kiosk` starts (waits for `control`): launches browser to `/face`
6. SPA loads, fetches `GET /api/state/snapshot`, opens WS

**Time to "Lafufu ready":** ~10s if Ollama is hot, ~60s on true cold boot. Admin UI shows a warmup banner so it's never silent.

### 5.2 Voice interaction lifecycle

User speaks → mic crosses RMS threshold:

```
agent: publish agent.state.listening
   ↓ all browsers: face=pulse indicator, admin=status badge
agent: record until silence
agent: Whisper → transcript
agent: publish agent.transcript ─────► admin: chat log entry
                                       face: optional caption
agent: publish agent.state.thinking ──► face: "thinking" vibe
agent: Ollama → reply
agent: publish agent.reply {text, emotion}
                          │
                          ├─► animator: set expression from emotion
                          ├─► control: log to chat
                          └─► printer: format + send to lp (if auto_print on)
agent: publish agent.state.speaking
agent: Piper → audio chunks → speaker (PyAudio singleton)
agent: per chunk, compute RMS → publish agent.tts.rms {ts, rms, mouth_target}
                          └─► animator: drive jaw setpoint
all chunks done:
agent: publish agent.state.idle
animator: jaw closes, returns to idle, publishes animator.event.gesture_done
```

**Key design decision:** `agent` owns audio playback, computes RMS in-process, publishes RMS only. Audio bytes never traverse the bus. Localhost NATS latency is ~1ms which is well under the 50ms lipsync window — sync is fine.

### 5.3 Live tuning — ephemeral preview vs durable commit

Two channels, both routed through `control`:

**Ephemeral preview** (live drag, no DB write):
```
Admin drags servo slider
  → browser WS msg: { topic: "animator.intent.preview", payload: {name, position} }
  → control republishes to NATS
  → animator moves servo immediately
  → animator publishes animator.pose @ 20Hz
  → all browsers see live position
```

**Durable commit** (slider released):
```
Browser sends REST: PATCH /api/settings/animator.head_lr.default { value: 2100 }
  → control writes to SQLite (single writer)
  → control publishes config.changed.animator.head_lr.default { value: 2100 }
  → animator updates internal default
  → all browsers reflect updated setting badge
```

Pattern reused everywhere: expression preview, TTS test phrases, volume sliders, etc.

### 5.4 Hot-swap lifecycle

```
browser → POST /api/system/services/agent/restart
control → systemctl restart lafufu-agent (subprocess)
control → publish system.service.restarting {name: "agent"}
              → browsers: "Agent restarting..." banner
systemd → SIGTERM agent → grace period → starts new process
new agent → connects NATS, warms up, publishes agent.state.idle
              → browsers: "Agent ready"
```

Meanwhile: animator + printer + control + face browser + admin browser all keep running. Lipsync times out in animator (no `agent.tts.rms` for 500ms) and jaw returns to closed.

Same pattern works for `animator`/`printer` restart. Restarting `control` is harder — browsers lose WS for 2-5s and auto-reconnect with JetStream backfill on state topics.

### 5.5 Browser connection / reconnection

```
Page load
  ├─ GET /api/state/snapshot  → seed UI with current state
  └─ Open WS /ws?subscribe=<comma-separated topic patterns>
        ├─ control registers NATS subs on browser's behalf
        ├─ replays JetStream backlog for state.* topics (~30s)
        └─ streams new messages

Network drop
  ├─ Browser detects WS close
  ├─ Exponential backoff (1s, 2s, 5s, 10s, cap 30s)
  └─ Reconnect → JetStream catches up missed state events
```

---

## 6. Error handling & supervision

### 6.1 systemd policies (per service)

```ini
[Service]
Restart=on-failure
RestartSec=5s
StartLimitBurst=5
StartLimitIntervalSec=60
TimeoutStopSec=10s
```

Crash + automatic restart with rate limiting. Manual override via `control` API for the admin UI.

### 6.2 NATS connection (every service via `BaseService`)

`connect_with_retry()`: exponential backoff (1s, 2s, 5s, 10s, 30s, 30s, …). Heartbeat task publishes `system.heartbeat.<name>` every 5s. `control` listens and marks any service with no heartbeat for **15s** as offline in the UI.

### 6.3 Hardware degradation (per service)

| Service | Hardware missing | Behavior |
|---|---|---|
| `agent` | Mic | Publish `agent.state.degraded`, accept text intents, still process LLM+TTS |
| `agent` | Ollama | Retry with backoff, surface error in admin UI, publish `agent.state.degraded.no_llm` |
| `agent` | Speaker | Continue (replies still printed/logged), publish `agent.state.degraded.no_audio` |
| `animator` | U2D2 | `animator.state.degraded` — accept intents, log them, publish synthetic pose |
| `printer` | No printer | `printer.state.offline`, noop on prints |
| `control` | SQLite corrupt | Refuse to start with clear error → manual restore from backup |

**Principle:** no service brings down others. Lafufu stays partially alive at every degradation level.

### 6.4 Application errors

- **Pydantic validates every NATS payload** on receive. Invalid → log + publish `system.error.<service>.bad_payload` → drop, don't crash.
- **FastAPI exceptions** auto-mapped to typed HTTP errors: `{error_code, message, details}`.
- **DB constraint violations** → 409 Conflict with details, never 500.
- **Unhandled exceptions** → caught by `BaseService` top-level handler → log + publish `system.error.<service>` → re-raise (systemd restarts).

### 6.5 Observability

- **Structured JSON logs** from every service to stdout → journald
- **`journalctl -u "lafufu-*" -f`** = full-system live tail
- **NATS `>` topic** in admin UI → live "system pulse" panel
- **Per-service status panel** in admin UI: heartbeat age, current state, last error, RAM/CPU
- **No Prometheus/Grafana** in Phase 0; admin UI status panel is the dashboard

### 6.6 Failure modes catalog

| Failure | Behavior | Recovery |
|---|---|---|
| `agent` crashes | systemd restarts in 5s. Animator/printer/UI unaffected. | Automatic |
| `animator` crashes | systemd restarts. Torque disabled on SIGTERM. Brief jaw freeze. | Automatic |
| `printer` crashes | systemd restarts. No effect on anything else. | Automatic |
| `control` crashes | Browsers lose WS for 2-5s, auto-reconnect. Voice loop continues. | Automatic |
| NATS crashes | Services log + retry. JetStream replays state.* on recovery. | Automatic |
| SQLite corrupted | `control` refuses to start with explicit error. | Manual restore from `/var/lafufu/db.sqlite.backup` |
| Pi power loss mid-write | SQLite WAL → consistent. Servos free-spin (no torque). | Boot recovers |
| U2D2 unplugged mid-run | `animator` detects, marks degraded, continues serving UI intents (not actuated) | Hot-plug re-detect on next intent |
| Wi-Fi drops | Local kiosk + voice unaffected. Remote browsers reconnect when net returns. | Automatic |
| Ollama hangs | Timeout (default 60s) → `agent.state.error` → return to idle, alert in UI | Automatic |
| Disk full | `control` refuses writes, publishes `system.error.disk_full` | Manual cleanup |

---

## 7. Testing strategy

### 7.1 Layer 1 — unit (fast, no I/O)

Pure functions only, per-package in `packages/<svc>/tests/`. Examples:

- `animator/tests/test_pose_math.py` — dxl ↔ deg conversions, clamps
- `animator/tests/test_expressions.py` — offset tables, curve sampling
- `animator/tests/test_lipsync.py` — RMS → mouth_target envelope
- `agent/tests/test_emotion_parser.py` — `[happy]` / `[sad]` tag extraction
- `agent/tests/test_vad.py` — silence detection on canned buffers
- `control/tests/test_models.py` — SQLModel relationships

Tools: `pytest`, `pytest-asyncio`, `hypothesis`. Full unit suite under 5s.

### 7.2 Layer 2 — integration (real NATS, fake hardware)

This is where 80% of confidence comes from.

`packages/shared/testing.py` provides fixtures:
- `nats_server` — real `nats-server` spawned in temp dir
- `fake_dxl_bus` — records writes, returns canned positions
- `fake_whisper` — canned transcript per audio file
- `fake_ollama` — scripted replies per prompt
- `fake_piper` — canned audio + RMS sequence

Test the contracts and interactions:
- Each service publishes the right topics with the right schemas
- `agent.reply.emotion=happy` → animator subscribes → sets correct expression
- `fake_dxl_bus.disconnect()` mid-run → animator publishes degraded, agent unaffected
- Restart `agent` mid-conversation → animator returns to idle gracefully
- `PATCH /api/settings/...` → `config.changed.*` published → service updates internal var

### 7.3 Layer 3 — API tests (FastAPI TestClient)

Standard FastAPI testing with `TestClient`. DB migrations tested both `upgrade head` and `downgrade -1` on temp DB.

### 7.4 Layer 4 — frontend tests (Phase 0 scope)

- **Vitest** on shared utilities (emotion → color, RMS → bar height, topic-pattern matcher)
- **Storybook** for component visual review (optional but valuable)
- **Playwright** deferred to Phase 2

### 7.5 Layer 5 — on-Pi smoke test (manual, scripted)

`scripts/smoke.sh` checks: all services active, heartbeats arriving, synthesized voice cycle works, expression triggers via API. Plus a manual checklist in `docs/SMOKE_CHECKLIST.md`.

### 7.6 CI

- **Every push:** unit + integration in GitHub Actions (no Pi, no Ollama — mocked)
- **Pre-deploy gate:** integration suite green
- **Post-deploy:** manual `smoke.sh` on Pi

### 7.7 TDD discipline

For Phase 0 build: write the integration test FIRST for each service interaction, watch it fail, then implement until it passes. For pure logic, classic red-green-refactor. Hardware-bound work goes straight to smoke checklist.

---

## 8. Deployment & operations

### 8.1 Pi setup (target state)

Hard-reset path (recommended for clean Phase 0 install):
1. Fresh Raspberry Pi OS Lite 64-bit on the Pi
2. Install: `python3.13`, `nats-server`, `cups`, `chromium-browser`, `labwc`, `uv`
3. Clone the new lafufu repo to `/srv/lafufu/`
4. Run `deploy/install.sh`:
   - Creates `/var/lafufu/` for DB + backups + JetStream storage
   - Installs systemd units to `/etc/systemd/system/`
   - Sets up CUPS for the printer
   - Sets up labwc autostart for `chromium-kiosk`
   - Enables `lafufu.target` and `nats.service`
   - Reboots into the running system

### 8.2 Filesystem layout on Pi

| Path | Purpose |
|---|---|
| `/srv/lafufu/` | The git checkout |
| `/var/lafufu/db.sqlite` | Control's SQLite DB |
| `/var/lafufu/jetstream/` | NATS JetStream file storage |
| `/var/lafufu/logs/` | Optional rotated journald copies |
| `/var/lafufu/db.sqlite.backup` | Periodic DB backup (cron in later phase) |
| `/etc/systemd/system/lafufu-*.service` | Service units |
| `/etc/systemd/system/lafufu.target` | Grouping target |
| `/etc/nats/nats-server.conf` | NATS config |

### 8.3 Update path

```bash
cd /srv/lafufu
git pull
uv sync
cd web && npm ci && npm run build && cd ..
sudo systemctl restart lafufu.target
```

`install.sh --update` wraps this.

---

## 9. Implementation roadmap (Phase 0)

Rough budget for one developer: **4–6 weeks of focused work.**

Suggested build order (each step ships a working slice):

1. **Repo skeleton** — `uv` workspace, packages stubs, CI scaffolding, NATS systemd unit
2. **`packages/shared`** — topic constants, base service template, schemas, NATS helper, test fixtures
3. **`animator` first** (no LLM dependency, easy to validate visually): DXL bus + idle pose + expression playback. Mock unit tests + real on-Pi smoke.
4. **`agent`** — port voice pipeline, integration tests with fake Whisper/Ollama/Piper
5. **`printer`** — CUPS integration
6. **`control` API** — DB models, REST endpoints for settings + service control, schema autogen build step
7. **`control` WS bridge** — NATS↔WebSocket fanout, subscription protocol
8. **`web/` SPA scaffold** — Vite + SolidJS + Tailwind, design system, NATS-WS client
9. **`/face` view** — minimal ambient state visualization (replaces mpv)
10. **`/admin` view** — settings tuning, live pose, service status, restart buttons
11. **systemd units + install script** + smoke test on Pi
12. **Hard-reset Pi, fresh install, end-to-end smoke** — Phase 0 done

---

## 10. Open decisions / deferred

| Item | Phase 0 stance | Decide by |
|---|---|---|
| New repo folder name | Recommend `C:\dev\lafufu` (existing is `lafufu-jb`) | Implementation start |
| Frontend framework final pick | Recommend SolidJS | Step 8 of build order |
| Auth model | Local-network-only, no auth in Phase 0 | Phase 2 (when remote admin is real) |
| DB backup automation | Manual in Phase 0 (`cp db.sqlite db.sqlite.backup`) | Phase 1 |
| Specific NATS config (max payload, etc.) | Defaults | Implementation as needed |
| Pi hard-reset vs install-in-place | Recommend hard reset for clean baseline | Pi work day |

---

## 11. Future phases preview

- **Phase 1:** Full expression editor with live preview (drag servo curves visually, see Lafufu react in real time)
- **Phase 2:** Behavior composition DSL (define triggers/responses without code), basic auth
- **Phase 3:** Pi-as-Wi-Fi-hotspot + spectator PWA for kids/audience interaction, queue management
- **Phase 4:** Plugin marketplace UI, Bluetooth, GPIO service, vision (camera), proper metrics

Each phase = one spec → one plan → one merge, building on this foundation.

---

*End of Phase 0 design spec.*
