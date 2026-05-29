"""Lipsync debug server — a small FastAPI app that drives the same
algorithms as the CLI scripts (00..03) from a browser, so you don't
have to edit Python or remember flag names at the bench.

Run on the Pi:

    sudo systemctl stop lafufu-agent lafufu-animator
    uv run python debug/lipsync/server.py
    # browse to http://<pi-ip>:8090/

Stop the lafufu services first so they don't compete for the bus or
the audio device.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Make `from algorithms import ...` work when launched as a script.
sys.path.insert(0, str(Path(__file__).parent))

from algorithms import (
    DirectCfg,
    EnvelopeCfg,
    GateCfg,
    MonolithCfg,
    ServoOnlyCfg,
    run_direct,
    run_envelope,
    run_gate,
    run_monolith,
    run_servo_only,
)

log = logging.getLogger("lipsync-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

AUDIO_DIR = Path("/tmp/lipsync")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Where Piper lives on the Pi. Edit if you have it somewhere else.
PIPER_BIN = Path("/srv/lafufu/.venv/bin/piper")
PIPER_MODEL = Path("/srv/lafufu/models/lafufu_voice.onnx")

app = FastAPI(title="Lafufu lipsync debug")


# --- run state (single user; one run at a time) ---

_run_lock = threading.Lock()
_active_thread: threading.Thread | None = None
_active_stop: threading.Event | None = None
_log: list[str] = []


def _start(target, *args) -> None:
    global _active_thread, _active_stop
    with _run_lock:
        if _active_thread is not None and _active_thread.is_alive():
            raise HTTPException(409, detail="a run is already in progress")
        stop = threading.Event()
        _active_stop = stop

        def _wrap():
            try:
                target(*args, stop=stop)
                _log.append("[done] run finished")
            except Exception as e:
                log.exception("run.failed")
                _log.append(f"[error] {e}")

        _active_thread = threading.Thread(target=_wrap, daemon=True)
        _active_thread.start()


# --- request models (pydantic for FastAPI body validation) ---


class ServoReq(BaseModel):
    freq_hz: float = ServoOnlyCfg.freq_hz
    duration_s: float = ServoOnlyCfg.duration_s
    tick_hz: int = ServoOnlyCfg.tick_hz


class _BaseChunkedReq(BaseModel):
    wav: str
    chunk_ms: int = DirectCfg.chunk_ms
    alsa_buffer_ms: int = DirectCfg.alsa_buffer_ms
    alsa_period_ms: int = DirectCfg.alsa_period_ms
    offset_ms: int = DirectCfg.offset_ms
    alsa_device: str = DirectCfg.alsa_device


class DirectReq(_BaseChunkedReq):
    rms_min: float = DirectCfg.rms_min
    rms_max: float = DirectCfg.rms_max


class EnvelopeReq(DirectReq):
    attack_ms: int = EnvelopeCfg.attack_ms
    release_ms: int = EnvelopeCfg.release_ms


class GateReq(_BaseChunkedReq):
    gate_threshold: float = GateCfg.gate_threshold
    open_pct: float = GateCfg.open_pct


class MonolithReq(BaseModel):
    wav: str
    fps: int = MonolithCfg.fps
    deadzone: float = MonolithCfg.deadzone
    gamma: float = MonolithCfg.gamma
    p_low: float = MonolithCfg.p_low
    p_high: float = MonolithCfg.p_high
    attack_ms: int = MonolithCfg.attack_ms
    release_ms: int = MonolithCfg.release_ms
    alsa_device: str = MonolithCfg.alsa_device


class GenReq(BaseModel):
    text: str
    filename: str = "test.wav"


# --- endpoints ---


@app.post("/api/run/servo")
def api_run_servo(req: ServoReq):
    _log.clear()
    _log.append(
        f"[start] servo_only freq_hz={req.freq_hz} duration_s={req.duration_s} "
        f"tick_hz={req.tick_hz}"
    )
    _start(run_servo_only, ServoOnlyCfg(**req.model_dump()))
    return {"ok": True}


def _check_wav(path: str) -> None:
    if not Path(path).is_file():
        raise HTTPException(400, detail=f"audio not found: {path}")


@app.post("/api/run/direct")
def api_run_direct(req: DirectReq):
    _check_wav(req.wav)
    _log.clear()
    _log.append(
        f"[start] direct wav={req.wav} offset_ms={req.offset_ms} rms=({req.rms_min}..{req.rms_max})"
    )
    cfg = DirectCfg(**req.model_dump(exclude={"wav"}))
    _start(run_direct, cfg, req.wav)
    return {"ok": True}


@app.post("/api/run/envelope")
def api_run_envelope(req: EnvelopeReq):
    _check_wav(req.wav)
    _log.clear()
    _log.append(
        f"[start] envelope wav={req.wav} offset_ms={req.offset_ms} "
        f"attack_ms={req.attack_ms} release_ms={req.release_ms}"
    )
    cfg = EnvelopeCfg(**req.model_dump(exclude={"wav"}))
    _start(run_envelope, cfg, req.wav)
    return {"ok": True}


@app.post("/api/run/gate")
def api_run_gate(req: GateReq):
    _check_wav(req.wav)
    _log.clear()
    _log.append(
        f"[start] gate wav={req.wav} offset_ms={req.offset_ms} "
        f"threshold={req.gate_threshold} open_pct={req.open_pct}"
    )
    cfg = GateCfg(**req.model_dump(exclude={"wav"}))
    _start(run_gate, cfg, req.wav)
    return {"ok": True}


@app.post("/api/run/monolith")
def api_run_monolith(req: MonolithReq):
    _check_wav(req.wav)
    _log.clear()
    _log.append(
        f"[start] monolith wav={req.wav} fps={req.fps} deadzone={req.deadzone} "
        f"gamma={req.gamma} attack_ms={req.attack_ms} release_ms={req.release_ms}"
    )
    cfg = MonolithCfg(**req.model_dump(exclude={"wav"}))
    _start(run_monolith, cfg, req.wav)
    return {"ok": True}


@app.post("/api/stop")
def api_stop():
    with _run_lock:
        if _active_stop is not None:
            _active_stop.set()
    _log.append("[stop] requested")
    return {"ok": True}


@app.post("/api/generate")
def api_generate(req: GenReq):
    if not PIPER_BIN.is_file() or not PIPER_MODEL.is_file():
        raise HTTPException(
            500,
            detail=(
                f"piper not found (expected {PIPER_BIN} and {PIPER_MODEL}); "
                "edit PIPER_BIN / PIPER_MODEL in server.py if it lives elsewhere"
            ),
        )
    # Sanitize filename — keep it under AUDIO_DIR, .wav suffix.
    safe = "".join(c for c in req.filename if c.isalnum() or c in "._-") or "test.wav"
    if not safe.endswith(".wav"):
        safe += ".wav"
    out = AUDIO_DIR / safe
    try:
        proc = subprocess.run(
            [str(PIPER_BIN), "--model", str(PIPER_MODEL), "--output_file", str(out)],
            input=req.text.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, detail="piper timed out") from None
    if proc.returncode != 0:
        raise HTTPException(
            500,
            detail={
                "error": "piper failed",
                "stderr": proc.stderr.decode(errors="replace")[-2000:],
            },
        )
    return {"path": str(out), "size": out.stat().st_size}


@app.get("/api/wavs")
def api_wavs():
    return {"files": sorted(str(p) for p in AUDIO_DIR.glob("*.wav"))}


@app.get("/api/status")
def api_status():
    running = _active_thread is not None and _active_thread.is_alive()
    return {"running": running, "log": _log[-100:]}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Lafufu lipsync debug</title>
<style>
:root {
  --bg: #1c1c1c;
  --panel: #262626;
  --panel-2: #2f2f2f;
  --text: #e8e8e8;
  --muted: #9aa0a6;
  --accent: #4ea3ff;
  --accent-hot: #ff7e4e;
  --good: #6bd96b;
  --bad: #ff6b6b;
  --border: #3a3a3a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.4;
}
header {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.6rem 1rem;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
header h1 { margin: 0; font-size: 1rem; font-weight: 600; }
header .spacer { flex: 1; }
#status { font-family: monospace; color: var(--muted); }
#status.running { color: var(--accent-hot); }
button {
  font: inherit;
  background: var(--panel-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.45rem 0.9rem;
  cursor: pointer;
}
button:hover { background: #3a3a3a; }
button.primary { background: var(--accent); color: #08152a; border-color: transparent; font-weight: 600; }
button.primary:hover { filter: brightness(1.1); }
button.danger { background: var(--bad); color: #1a0000; border-color: transparent; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
main { padding: 1rem; max-width: 980px; margin: 0 auto; }
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  margin-bottom: 1rem;
}
.card h2 { margin: 0 0 0.5rem; font-size: 0.95rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.row { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.6rem 1rem;
}
label.field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  font-size: 0.8rem;
  color: var(--muted);
}
label.field span.label { font-weight: 600; color: var(--text); }
input[type=text], input[type=number], select {
  font: inherit;
  font-family: ui-monospace, "Cascadia Code", Menlo, monospace;
  background: var(--panel-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.35rem 0.5rem;
  width: 100%;
}
.tabs { display: flex; gap: 0.25rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
.tabs button.active { background: var(--accent); color: #08152a; border-color: transparent; font-weight: 600; }
.tab-body { display: none; }
.tab-body.active { display: block; }
.tab-body p.help { color: var(--muted); margin-top: 0; }
.tab-body p.apply { color: var(--muted); font-size: 0.85rem; margin: 0.4rem 0 0; }
.tab-body p.apply code { background: var(--panel-2); padding: 0 0.3rem; border-radius: 3px; color: var(--accent); }
pre#log {
  font-family: ui-monospace, "Cascadia Code", Menlo, monospace;
  background: #111;
  color: #cfd8dc;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.5rem;
  max-height: 18rem;
  overflow: auto;
  white-space: pre-wrap;
  margin: 0;
  font-size: 12px;
}
.wav-current { font-family: monospace; color: var(--muted); margin-left: 0.5rem; }
.wav-current.set { color: var(--good); }
dialog#export-dialog {
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  max-width: 720px;
  width: 92%;
}
dialog#export-dialog::backdrop { background: rgba(0,0,0,0.6); }
dialog#export-dialog h3 { margin: 0 0 0.6rem; font-size: 1rem; }
dialog#export-dialog textarea {
  font-family: ui-monospace, "Cascadia Code", Menlo, monospace;
  background: #111;
  color: #cfd8dc;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.5rem;
  width: 100%;
  font-size: 12px;
  resize: vertical;
}
dialog#export-dialog .actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.6rem;
}
.copied-flash { border-color: var(--good) !important; }
</style>
</head>
<body>
<header>
  <h1>Lafufu lipsync debug</h1>
  <div class="spacer"></div>
  <span id="status">idle</span>
  <button class="danger" onclick="stopRun()">Stop</button>
</header>

<main>

<div class="card">
  <h2>Audio</h2>
  <p style="margin-top: 0; color: var(--muted);">
    Generate a fresh WAV with Piper, or pick a previously generated one.
    The selected file is used by Direct / Envelope / Gate.
  </p>
  <div class="grid">
    <label class="field">
      <span class="label">Generate from text</span>
      <input type="text" id="gen-text" value="this is a test of the mouth sync" />
    </label>
    <label class="field">
      <span class="label">Filename</span>
      <input type="text" id="gen-name" value="test.wav" />
    </label>
  </div>
  <div class="row" style="margin-top: 0.6rem;">
    <button onclick="generate()">Generate</button>
    <span style="flex: 1;"></span>
    <label class="field" style="min-width: 220px;">
      <span class="label">Pick existing</span>
      <select id="wav-list" onchange="onWavSelect()"></select>
    </label>
    <button onclick="refreshWavs()">Refresh</button>
  </div>
  <div style="margin-top: 0.5rem;">
    selected: <span id="wav-current" class="wav-current">none</span>
  </div>
</div>

<div class="card">
  <div class="tabs">
    <button data-tab="servo" onclick="showTab('servo')">00 Servo only</button>
    <button data-tab="direct" onclick="showTab('direct')">01 Direct</button>
    <button data-tab="envelope" onclick="showTab('envelope')">02 Envelope</button>
    <button data-tab="gate" onclick="showTab('gate')">03 Gate</button>
    <button data-tab="monolith" onclick="showTab('monolith')">04 Monolith (legacy)</button>
  </div>

  <div id="tab-servo" class="tab-body">
    <p class="help">
      No audio. Sweep the jaw open&lt;-&gt;closed at <code>freq_hz</code>. Run this
      first at 2, 4, 6, 8 Hz &mdash; find the frequency at which the servo starts
      to lag / overshoot / quantize. That's the ceiling for any
      amplitude-following lipsync.
    </p>
    <div class="grid">
      <label class="field"><span class="label">Frequency (Hz)</span><input type="number" id="servo-freq-hz" value="4.0" step="0.5" min="0.5" max="20" /></label>
      <label class="field"><span class="label">Duration (s)</span><input type="number" id="servo-duration-s" value="5.0" step="0.5" min="1" max="30" /></label>
      <label class="field"><span class="label">Tick rate (Hz)</span><input type="number" id="servo-tick-hz" value="30" step="5" min="10" max="60" /></label>
    </div>
    <div style="margin-top: 0.8rem;">
      <button class="primary" onclick="runServo()">Run</button>
      <button onclick="exportMode('servo')">Export settings&hellip;</button>
    </div>
  </div>

  <div id="tab-direct" class="tab-body">
    <p class="help">
      RMS -&gt; jaw, instant. No envelope. Tune <code>offset_ms</code> and
      <code>rms_min/max</code> here &mdash; a baseline for the other modes.
    </p>
    <div class="grid">
      <label class="field"><span class="label">Chunk (ms)</span><input type="number" id="d-chunk-ms" value="40" min="10" max="200" step="5" /></label>
      <label class="field"><span class="label">ALSA buffer (ms)</span><input type="number" id="d-alsa-buffer-ms" value="1000" min="100" max="4000" step="50" /></label>
      <label class="field"><span class="label">ALSA period (ms)</span><input type="number" id="d-alsa-period-ms" value="40" min="10" max="500" step="10" /></label>
      <label class="field"><span class="label">Offset (ms, +ahead/-behind)</span><input type="number" id="d-offset-ms" value="0" min="-500" max="500" step="20" /></label>
      <label class="field"><span class="label">RMS min</span><input type="number" id="d-rms-min" value="0.005" step="0.001" min="0" max="0.5" /></label>
      <label class="field"><span class="label">RMS max</span><input type="number" id="d-rms-max" value="0.30" step="0.01" min="0.05" max="1" /></label>
      <label class="field"><span class="label">ALSA device</span><input type="text" id="d-alsa-device" value="default" /></label>
    </div>
    <p class="apply">apply to lafufu: <code>animator.lipsync.offset_ms</code>, agent's RMS calibration.</p>
    <div style="margin-top: 0.8rem;">
      <button class="primary" onclick="runDirect()">Run</button>
      <button onclick="exportMode('direct')">Export settings&hellip;</button>
    </div>
  </div>

  <div id="tab-envelope" class="tab-body">
    <p class="help">
      Direct + attack/release envelope. Closest to the production agent.
      The numbers you find here go straight into the live settings.
    </p>
    <div class="grid">
      <label class="field"><span class="label">Chunk (ms)</span><input type="number" id="e-chunk-ms" value="40" min="10" max="200" step="5" /></label>
      <label class="field"><span class="label">ALSA buffer (ms)</span><input type="number" id="e-alsa-buffer-ms" value="1000" min="100" max="4000" step="50" /></label>
      <label class="field"><span class="label">ALSA period (ms)</span><input type="number" id="e-alsa-period-ms" value="40" min="10" max="500" step="10" /></label>
      <label class="field"><span class="label">Offset (ms)</span><input type="number" id="e-offset-ms" value="0" min="-500" max="500" step="20" /></label>
      <label class="field"><span class="label">RMS min</span><input type="number" id="e-rms-min" value="0.005" step="0.001" min="0" max="0.5" /></label>
      <label class="field"><span class="label">RMS max</span><input type="number" id="e-rms-max" value="0.30" step="0.01" min="0.05" max="1" /></label>
      <label class="field"><span class="label">Attack (ms)</span><input type="number" id="e-attack-ms" value="20" min="1" max="200" step="5" /></label>
      <label class="field"><span class="label">Release (ms)</span><input type="number" id="e-release-ms" value="80" min="5" max="400" step="10" /></label>
      <label class="field"><span class="label">ALSA device</span><input type="text" id="e-alsa-device" value="default" /></label>
    </div>
    <p class="apply">apply to lafufu: <code>animator.lipsync.attack_ms</code>, <code>animator.lipsync.release_ms</code>, <code>animator.lipsync.offset_ms</code>.</p>
    <div style="margin-top: 0.8rem;">
      <button class="primary" onclick="runEnvelope()">Run</button>
      <button onclick="exportMode('envelope')">Export settings&hellip;</button>
    </div>
  </div>

  <div id="tab-gate" class="tab-body">
    <p class="help">
      Binary open/close on an RMS threshold. If the GATE looks tight but
      DIRECT/ENVELOPE don't, the desync is in amplitude tracking, not
      timing.
    </p>
    <div class="grid">
      <label class="field"><span class="label">Chunk (ms)</span><input type="number" id="g-chunk-ms" value="40" min="10" max="200" step="5" /></label>
      <label class="field"><span class="label">ALSA buffer (ms)</span><input type="number" id="g-alsa-buffer-ms" value="1000" min="100" max="4000" step="50" /></label>
      <label class="field"><span class="label">ALSA period (ms)</span><input type="number" id="g-alsa-period-ms" value="40" min="10" max="500" step="10" /></label>
      <label class="field"><span class="label">Offset (ms)</span><input type="number" id="g-offset-ms" value="0" min="-500" max="500" step="20" /></label>
      <label class="field"><span class="label">Gate threshold (RMS)</span><input type="number" id="g-gate-threshold" value="0.02" step="0.005" min="0" max="0.5" /></label>
      <label class="field"><span class="label">Open pct (0..1)</span><input type="number" id="g-open-pct" value="1.0" step="0.05" min="0" max="1" /></label>
      <label class="field"><span class="label">ALSA device</span><input type="text" id="g-alsa-device" value="default" /></label>
    </div>
    <div style="margin-top: 0.8rem;">
      <button class="primary" onclick="runGate()">Run</button>
      <button onclick="exportMode('gate')">Export settings&hellip;</button>
    </div>
  </div>

  <div id="tab-monolith" class="tab-body">
    <p class="help">
      <b>Legacy monolith reference.</b> Faithful port of
      <code>C:\\dev\\lafufu-jb\\dynamixel.py:1838-1955</code> &mdash; the
      known-working lipsync. Uses <b>per-WAV percentile-normalised RMS</b>
      (content-adaptive), an explicit <b>deadzone + gamma</b> curve,
      <b>file-mode aplay</b> (not stdin streaming), and <b>wall-clock motor
      pacing</b>. If this looks tight but Envelope doesn't, port these
      pieces into the production agent. Defaults match the legacy.
    </p>
    <div class="grid">
      <label class="field"><span class="label">FPS (chunks/sec)</span><input type="number" id="m-fps" value="20" min="5" max="60" step="5" /></label>
      <label class="field"><span class="label">Deadzone (0..1)</span><input type="number" id="m-deadzone" value="0.05" step="0.01" min="0" max="0.5" /></label>
      <label class="field"><span class="label">Gamma (&lt;1 = perceptual)</span><input type="number" id="m-gamma" value="0.70" step="0.05" min="0.1" max="3" /></label>
      <label class="field"><span class="label">p_low (RMS floor pct)</span><input type="number" id="m-p-low" value="0.10" step="0.05" min="0" max="0.5" /></label>
      <label class="field"><span class="label">p_high (RMS ceil pct)</span><input type="number" id="m-p-high" value="0.95" step="0.01" min="0.5" max="1" /></label>
      <label class="field"><span class="label">Attack (ms)</span><input type="number" id="m-attack-ms" value="30" min="1" max="200" step="5" /></label>
      <label class="field"><span class="label">Release (ms)</span><input type="number" id="m-release-ms" value="80" min="5" max="400" step="10" /></label>
      <label class="field"><span class="label">ALSA device</span><input type="text" id="m-alsa-device" value="default" /></label>
    </div>
    <p class="apply">apply to lafufu: <code>animator.lipsync.attack_ms</code>, <code>animator.lipsync.release_ms</code>; the other pieces (percentiles, deadzone, gamma, file-mode aplay) require code changes &mdash; see <code>legacy-comparison.md</code>.</p>
    <div style="margin-top: 0.8rem;">
      <button class="primary" onclick="runMonolith()">Run</button>
      <button onclick="exportMode('monolith')">Export settings&hellip;</button>
    </div>
  </div>
</div>

<div class="card">
  <h2>Log</h2>
  <pre id="log">(idle)</pre>
</div>

</main>

<dialog id="export-dialog">
  <h3>Settings export</h3>
  <p style="color: var(--muted); margin: 0 0 0.6rem; font-size: 0.85rem;">
    Edit the &ldquo;Notes&rdquo; line if you want, then click <b>Copy</b>.
    The whole block is markdown &mdash; paste it to another agent to apply
    the live settings on Lafufu and (optionally) port the other knobs.
  </p>
  <textarea id="export-text" rows="22" spellcheck="false"></textarea>
  <div class="actions">
    <button onclick="document.getElementById('export-dialog').close()">Close</button>
    <button class="primary" onclick="copyExport()">Copy to clipboard</button>
  </div>
</dialog>

<script>
let selectedWav = "";

function num(id) {
  const v = parseFloat(document.getElementById(id).value);
  if (Number.isNaN(v)) throw new Error("missing/invalid: " + id);
  return v;
}
function intv(id) { return Math.round(num(id)); }
function str(id) { return document.getElementById(id).value; }

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = (j && j.detail) ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : r.statusText;
    throw new Error(msg);
  }
  return j;
}

function showTab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-body").forEach(el => el.classList.toggle("active", el.id === "tab-" + name));
}
showTab("servo");

async function refreshWavs() {
  try {
    const j = await api("GET", "/api/wavs");
    const sel = document.getElementById("wav-list");
    sel.replaceChildren();
    const blank = document.createElement("option");
    blank.value = ""; blank.textContent = "-- pick a wav --";
    sel.appendChild(blank);
    for (const f of j.files) {
      const opt = document.createElement("option");
      opt.value = f; opt.textContent = f;
      sel.appendChild(opt);
    }
    if (selectedWav) sel.value = selectedWav;
  } catch (e) { addLog("[error] refresh wavs: " + e.message); }
}
function onWavSelect() {
  selectedWav = document.getElementById("wav-list").value;
  const cur = document.getElementById("wav-current");
  cur.textContent = selectedWav || "none";
  cur.classList.toggle("set", !!selectedWav);
}
function requireWav() {
  if (!selectedWav) { alert("Pick or generate a WAV first."); throw new Error("no wav"); }
  return selectedWav;
}

async function generate() {
  try {
    addLog("[generate] piper...");
    const j = await api("POST", "/api/generate", {
      text: str("gen-text"),
      filename: str("gen-name"),
    });
    addLog("[generate] wrote " + j.path + " (" + j.size + " bytes)");
    await refreshWavs();
    selectedWav = j.path;
    document.getElementById("wav-list").value = j.path;
    onWavSelect();
  } catch (e) { addLog("[error] generate: " + e.message); }
}

async function runServo() {
  try {
    await api("POST", "/api/run/servo", {
      freq_hz: num("servo-freq-hz"),
      duration_s: num("servo-duration-s"),
      tick_hz: intv("servo-tick-hz"),
    });
  } catch (e) { addLog("[error] run servo: " + e.message); }
}
function directBody(prefix) {
  return {
    wav: requireWav(),
    chunk_ms: intv(prefix + "-chunk-ms"),
    alsa_buffer_ms: intv(prefix + "-alsa-buffer-ms"),
    alsa_period_ms: intv(prefix + "-alsa-period-ms"),
    offset_ms: intv(prefix + "-offset-ms"),
    rms_min: num(prefix + "-rms-min"),
    rms_max: num(prefix + "-rms-max"),
    alsa_device: str(prefix + "-alsa-device"),
  };
}
async function runDirect() {
  try { await api("POST", "/api/run/direct", directBody("d")); }
  catch (e) { addLog("[error] run direct: " + e.message); }
}
async function runEnvelope() {
  try {
    const body = directBody("e");
    body.attack_ms = intv("e-attack-ms");
    body.release_ms = intv("e-release-ms");
    await api("POST", "/api/run/envelope", body);
  } catch (e) { addLog("[error] run envelope: " + e.message); }
}
async function runMonolith() {
  try {
    await api("POST", "/api/run/monolith", {
      wav: requireWav(),
      fps: intv("m-fps"),
      deadzone: num("m-deadzone"),
      gamma: num("m-gamma"),
      p_low: num("m-p-low"),
      p_high: num("m-p-high"),
      attack_ms: intv("m-attack-ms"),
      release_ms: intv("m-release-ms"),
      alsa_device: str("m-alsa-device"),
    });
  } catch (e) { addLog("[error] run monolith: " + e.message); }
}
async function runGate() {
  try {
    await api("POST", "/api/run/gate", {
      wav: requireWav(),
      chunk_ms: intv("g-chunk-ms"),
      alsa_buffer_ms: intv("g-alsa-buffer-ms"),
      alsa_period_ms: intv("g-alsa-period-ms"),
      offset_ms: intv("g-offset-ms"),
      gate_threshold: num("g-gate-threshold"),
      open_pct: num("g-open-pct"),
      alsa_device: str("g-alsa-device"),
    });
  } catch (e) { addLog("[error] run gate: " + e.message); }
}
async function stopRun() {
  try { await api("POST", "/api/stop"); } catch (e) { addLog("[error] stop: " + e.message); }
}

function addLog(line) {
  const pre = document.getElementById("log");
  if (pre.textContent === "(idle)") pre.textContent = "";
  pre.textContent += line + "\\n";
  pre.scrollTop = pre.scrollHeight;
}

let lastLogLen = 0;
async function pollStatus() {
  try {
    const j = await api("GET", "/api/status");
    const st = document.getElementById("status");
    st.textContent = j.running ? "running" : "idle";
    st.classList.toggle("running", j.running);
    if (j.log.length !== lastLogLen) {
      const pre = document.getElementById("log");
      pre.textContent = j.log.length ? j.log.join("\\n") : "(idle)";
      pre.scrollTop = pre.scrollHeight;
      lastLogLen = j.log.length;
    }
  } catch (_) {}
}
function exportMode(mode) {
  let text;
  try { text = formatSummary(mode); }
  catch (e) { alert("Couldn't read settings: " + e.message); return; }
  document.getElementById("export-text").value = text;
  document.getElementById("export-dialog").showModal();
}

function formatSummary(mode) {
  const now = new Date().toISOString();
  const wav = selectedWav || "(none selected)";
  if (mode === "servo") {
    return `# Lipsync export - servo only (no audio)

**Date:** ${now}
**Notes:** (replace this line with your observations, e.g. "tracks cleanly to 6 Hz, jitter at 8 Hz")

## Knobs

- freq_hz = ${num("servo-freq-hz")}
- duration_s = ${num("servo-duration-s")}
- tick_hz = ${intv("servo-tick-hz")}

## Context

Servo-only mode (no audio). Used to characterise the jaw servo's response.
The highest freq_hz the servo can still track cleanly is the practical
ceiling for any amplitude-following lipsync. No direct lafufu settings to
apply; informational only.

Source: debug/lipsync/algorithms.py (run_servo_only + ServoOnlyCfg).
`;
  }
  if (mode === "monolith") {
    return `# Lipsync export - monolith mode (legacy reference)

**Audio:** ${wav}
**Date:** ${now}
**Notes:** (replace this line with your observations)

## Apply to lafufu (live settings via admin UI or PUT /api/settings/<key>)

- animator.lipsync.attack_ms = ${intv("m-attack-ms")}
- animator.lipsync.release_ms = ${intv("m-release-ms")}

## Code changes required (the legacy pieces not in production yet)

The legacy algorithm beats Envelope mode primarily because of these layers,
which the production agent does NOT have. Port them into the agent's
lipsync RMS path:

- fps = ${intv("m-fps")}  (chunk_ms = 1000/fps = ${Math.round(1000 / Math.max(1, intv("m-fps")))} ms)
- deadzone = ${num("m-deadzone")}  (x <= deadzone -> target=0)
- gamma = ${num("m-gamma")}  (target = target ** gamma, perceptual curve)
- p_low = ${num("m-p-low")}  (RMS floor percentile, computed PER WAV)
- p_high = ${num("m-p-high")}  (RMS ceiling percentile, computed PER WAV)
- alsa_device = ${str("m-alsa-device")}

## Context

Algorithm: faithful port of the legacy monolith at
**C:/dev/lafufu-jb/dynamixel.py:1838-1955** (known-working before the
modular rewrite). Source for this testbed: **debug/lipsync/algorithms.py**
(run_monolith + MonolithCfg).

Key differences vs production / Envelope mode:

1. **Content-adaptive RMS normalisation** (the big win): the legacy
   computes p_low/p_high percentiles over the WHOLE WAV in a pre-pass,
   then maps each chunk's RMS into that per-utterance range. Production
   uses fixed rms_min/rms_max so a quiet WAV never opens the mouth fully.
2. **Deadzone + gamma curve**: kills the "mouth flutters in pauses" and
   "mouth undershoots on quiet consonants" effects.
3. **File-mode aplay** (aplay file.wav) instead of stdin streaming:
   aplay manages its own buffering at full speed and the motor loop paces
   against its own wall clock, so motor + audio drift TOGETHER under
   system load rather than against each other.
4. **Wall-clock motor pacing** against t0 (the moment aplay was spawned).

For an implementing agent:
- Pure tunables (attack_ms, release_ms): set via admin UI now.
- Algorithm work: edit **packages/agent/src/lafufu_agent/pipeline.py**
  (the lipsync RMS path) to add the percentile pre-pass, deadzone, and
  gamma. Switching production to file-mode aplay is a deeper change in
  **packages/agent/src/lafufu_agent/__main__.py** (_AplayPlayer) and may
  not be worth it if the algorithm changes alone close the gap.
- See **debug/lipsync/legacy-comparison.md** for the full side-by-side.
- See **docs/superpowers/plans/2026-05-28-prod-hardening.md** (T13) for
  the broader follow-up.
`;
  }
  const prefix = {direct: "d", envelope: "e", gate: "g"}[mode];
  const offset = intv(prefix + "-offset-ms");
  const live = [`- animator.lipsync.offset_ms = ${offset}`];
  if (mode === "envelope") {
    live.push(`- animator.lipsync.attack_ms = ${intv("e-attack-ms")}`);
    live.push(`- animator.lipsync.release_ms = ${intv("e-release-ms")}`);
  }
  const other = [
    `- chunk_ms = ${intv(prefix + "-chunk-ms")}`,
    `- alsa_buffer_ms = ${intv(prefix + "-alsa-buffer-ms")}`,
    `- alsa_period_ms = ${intv(prefix + "-alsa-period-ms")}`,
    `- alsa_device = ${str(prefix + "-alsa-device")}`,
  ];
  if (mode === "direct" || mode === "envelope") {
    other.push(`- rms_min = ${num(prefix + "-rms-min")}`);
    other.push(`- rms_max = ${num(prefix + "-rms-max")}`);
  }
  if (mode === "gate") {
    other.push(`- gate_threshold = ${num("g-gate-threshold")}`);
    other.push(`- open_pct = ${num("g-open-pct")}`);
  }
  const gateNote = (mode === "gate")
    ? `\n\nNote: gate is binary open/close - no direct production equivalent. If gate looks tight but envelope doesn't, the desync is in amplitude tracking, not timing.`
    : "";
  return `# Lipsync export - ${mode} mode

**Audio:** ${wav}
**Date:** ${now}
**Notes:** (replace this line with your observations)

## Apply to lafufu (live settings via admin UI or PUT /api/settings/<key>)

${live.join("\n")}

## Other knobs (used in the testbed; may require code changes)

${other.join("\n")}

## Context

Algorithm: ${mode} (source: debug/lipsync/algorithms.py - run_${mode} + ${mode[0].toUpperCase() + mode.slice(1)}Cfg).${gateNote}

For an implementing agent: apply the live settings above first and test
on the running Lafufu with the same audio file. If desync remains, the
"other knobs" may need code changes:

- alsa_buffer_ms / alsa_period_ms / alsa_device live in
  packages/agent/src/lafufu_agent/__main__.py (_AplayPlayer class).
- rms_min / rms_max and chunk_ms live in the agent's lipsync
  RMS path (packages/agent/src/lafufu_agent/pipeline.py).
- See docs/superpowers/plans/2026-05-28-prod-hardening.md (T13) for
  the broader follow-up about extra desync added by our own layers
  (motion smoother, asyncio scheduling, NATS fan-out).
`;
}

async function copyExport() {
  const ta = document.getElementById("export-text");
  try {
    await navigator.clipboard.writeText(ta.value);
    ta.classList.add("copied-flash");
    setTimeout(() => ta.classList.remove("copied-flash"), 800);
  } catch (_) {
    ta.select();
    document.execCommand("copy");
  }
}

setInterval(pollStatus, 500);
refreshWavs();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")
