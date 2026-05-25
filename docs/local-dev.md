# Local development — running Lafufu on your laptop

Exercise the full voice loop — wake word, opening phrase, multi-round
conversation, optional print intent — using your laptop's mic, speakers,
and the SolidJS admin UI. No Pi required. Pi deployment is unaffected.

## Prerequisites

| Component | How to install |
|---|---|
| Python 3.13 + `uv` | already set up if you've been running tests |
| Docker | https://www.docker.com/products/docker-desktop |
| Node.js (18+) | https://nodejs.org/ |
| Mic + speakers | laptop built-ins are fine |

## One-time setup

```powershell
# 1. Sync workspace + the optional wakeword extra
uv sync --all-packages --all-extras

# 2. Bring up NATS + Ollama (downloads qwen2.5:1.5b on first run, ~1 GB)
./scripts/dev-up.ps1

# 3. Grab a default Piper voice (~25 MB)
./scripts/get_piper_voice.ps1

# 4. Build the web SPA once so the control service can serve it,
#    OR skip this and use the Vite dev server (Terminal 4 below).
cd web; npm install; npm run build; cd ..
```

## Running the stack

Open three or four terminals from the repo root.

### Terminal 1 — dev stack (background)
```powershell
./scripts/dev-up.ps1
```
Brings up NATS + Ollama. Already idempotent — re-run after a reboot.

### Terminal 2 — control service
```powershell
uv run python -m lafufu_control
```
Serves the API and WS bridge on http://localhost:8080.

### Terminal 3 — agent (the voice loop)
```powershell
$env:LAFUFU_PIPER_MODEL    = "$(Resolve-Path .\models\en_US-amy-medium.onnx)"
$env:LAFUFU_WAKEWORD_ENABLED = "1"
$env:LAFUFU_WAKEWORD_MODEL   = "hey_jarvis_v0.1"
$env:LAFUFU_INTERACTION_MODE = "trigger"
$env:LAFUFU_TRIGGER_ROUNDS   = "2"
$env:LAFUFU_TRIGGER_PHRASE   = "Tell me what troubles you, traveler."
$env:LAFUFU_TRIGGER_PRINT    = "none"
$env:LAFUFU_LLM_MODEL        = "qwen2.5:1.5b"
uv run python -m lafufu_agent
```

`hey_jarvis_v0.1` is the openwakeword default until the custom
`hey_lafufu.onnx` is trained on Maven's box. The `none` print mode skips
the receipt printer intent (no printer locally).

### Terminal 4 — Vite dev server (optional, for live reload)
```powershell
cd web
$env:LAFUFU_HOST = "localhost:8080"
npm run dev
```
Opens the SolidJS admin UI on http://localhost:5173 with hot-reload.
Skip this terminal if you ran `npm run build` — the control service
serves the built bundle from http://localhost:8080.

## What to test

1. Open the admin chat widget — http://localhost:5173/admin (or
   :8080/admin if you skipped Vite).
2. Watch the agent log — `agent.state.idle` should appear after warmup.
3. Say **"hey jarvis"** clearly into the mic. The agent transitions to
   `speaking` and you should hear the opening phrase through your
   speakers.
4. Speak your first answer when the opening line ends. The widget should
   light up `transcribing` → `thinking` → `speaking` and the
   transcript + reply land in the chat history.
5. Speak the follow-up. **Round 2's reply should reference what you said
   in round 1** — that's the v2 personalization feature (PR #12).

## Switching modes / settings without restarting agent

Most knobs live in `config.changed.*` NATS subjects (see
`packages/control/src/lafufu_control/bootstrap.py` for the full set).
Voice model, STT backend, system prompt, volume, etc. all hot-swap. The
trigger-mode env vars (`LAFUFU_TRIGGER_*`) currently require an agent
restart — they'll move to DB settings in a follow-up.

## Tearing down

```powershell
docker compose -f docker-compose.dev.yml down
```
Add `-v` to also remove the `ollama-data` volume (forces re-download
of the LLM on next `dev-up`).

## Troubleshooting

- **`OSError: [Errno -9996] Invalid input device`** (or similar PyAudio
  open failure) — another process is holding the mic. Close
  Zoom/Teams/Chrome tabs that requested mic access.
- **Repeated `nats.connect.failed attempt=N` warnings in the agent log**
  — NATS isn't reachable. The service retries forever rather than
  exiting, so the log will keep streaming these. Verify the container
  is up with `docker compose -f docker-compose.dev.yml ps` and restart
  with `docker compose -f docker-compose.dev.yml restart nats`.
- **Ollama is slow** — qwen2.5:1.5b on CPU takes ~1-3 s per reply on a
  modern laptop. If you have NVIDIA + the container toolkit set up, add
  `deploy.resources.reservations.devices` for GPU passthrough in
  `docker-compose.dev.yml`.
- **No reply, agent stuck in `thinking`** — make sure the model name in
  `LAFUFU_LLM_MODEL` matches what the Ollama container has (`docker
  compose -f docker-compose.dev.yml exec ollama ollama list`).
- **No sound** — the agent uses `aplay` on Linux; on Windows it falls
  back to a direct PyAudio output device. Check `LAFUFU_APLAY_DEVICE`
  or set the system default playback device.

## What this stack does NOT include

- The animator service (servo control) — not useful without hardware
- The printer service — not useful without the thermal receipt printer
- Pi-specific Bluetooth IP-discovery (lafufu-btcast)

The agent publishes events for animator and printer over NATS as usual;
nothing subscribes locally, which is fine — the trigger loop completes
without them.
