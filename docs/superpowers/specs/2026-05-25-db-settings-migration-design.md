# DB-Backed Settings Migration + Admin Tab Reorg — Design

**Date:** 2026-05-25
**Status:** Approved (brainstorm) → ready for implementation plan

## Goal

Promote every operator-tunable knob to DB-backed settings reachable from the
admin UI on any device (iPhone, laptop, kiosk). Today the trigger-mode loop,
wake-word gate, and mic device selection live entirely in env vars — flipping
modes requires SSH + service restart. After this work, the same controls
appear in the admin settings form, swap live via `config.changed.*`, and
survive restarts.

Concurrently, reorganize the settings form so tabs match service namespaces
(`agent | animator | audio | printer | other`) and the tab + search header
stays pinned while scrolling — current ad-hoc grouping (audio mixed with
agent.stt) drifts every time a new setting lands.

## Background

The DB-backed settings pattern is mature: ControlService seeds defaults in
`bootstrap.py`, publishes `config.changed.<key>` on writes, and every other
service subscribes via `nats_helper.subscribe_model` to hot-swap behavior
without restart. STT backend, voice model, system prompt, speaker volume —
all already work this way. The admin form discovers settings via
`GET /api/settings` and renders dropdowns from `DYNAMIC_OPTIONS` fetchers
(`/api/agent/voices`, `/api/agent/models`, `/api/agent/whisper-models`).

Three batches of operator knobs never made the migration:

1. **Trigger-mode loop** (PR #11) — `LAFUFU_INTERACTION_MODE`,
   `LAFUFU_TRIGGER_{PHRASE,EMOTION,ROUNDS,PRINT_MODE,PRINT_PROMPT}` (6 vars).
   The spec explicitly deferred DB-backed config to a follow-up PR.
2. **Wake-word gate** (PR #10) — `LAFUFU_WAKEWORD_{ENABLED,MODEL,THRESHOLD}`
   (3 vars). Same deferral.
3. **Mic device selection** — `LAFUFU_INPUT_DEVICE` (and the operator-host
   `LAFUFU_INPUT_DEVICE_{PREFER,AVOID}` lists, which stay env-only per
   below).

The animator side also has 5 servo-default subscribers
(`animator.{head_lr,head_ud,eye,jaw,brow}.default`) wired in
`packages/animator/src/lafufu_animator/service.py:166-172` that the admin
never exposed — they're orphan capabilities until someone seeds rows and
adds a UI surface.

## Scope

In scope:

- 9 new `agent.*` settings (5 trigger + 3 wakeword + 1 input_device) seeded
  in `bootstrap.py` with sensible defaults, plus matching agent-service
  subscribers for live swap.
- 5 new `animator.*` servo-default settings seeded to the canonical idle
  pose; animator subscribers already exist.
- 1 new endpoint `GET /api/agent/input-devices` mirroring the voices /
  whisper-models / stt-backends pattern.
- Tab reorg in `settings_form.tsx`: `agent | animator | audio | printer |
  other`. `categoryOf(key)` reduces to a prefix-based rule with one explicit
  carve-out (audio bundles `speaker.*` + `tts.*`).
- Sticky tab + search header so they stay pinned while the settings list
  scrolls.
- Fix the `_rebuild_tts` resource leak — old `_PyAudioPlayer` isn't closed
  before assignment, leaking a WASAPI stream + `pyaudio.PyAudio()` instance
  on every voice swap (no impact on Pi's aplay path; live for Windows/macOS
  dev).
- Tests: live-swap integration tests for each of the 9 agent subscribers
  (mirror `test_voice_model_change_swaps_piper_instance`); a tab-assignment
  unit test; a `_rebuild_tts` close test.

Out of scope (deliberate — for future PRs):

- `LAFUFU_INPUT_DEVICE_PREFER` and `_AVOID` stay env-only. They're
  per-operator-host hardware lists, not per-instance config; surfacing them
  as DB settings would require multi-host config which the platform doesn't
  model today.
- `LAFUFU_WAKEWORD_ENABLED=1` still controls *whether the agent imports
  openwakeword on startup* — that bit can't move to DB because pulling the
  dep at startup is a process-level decision. The DB setting becomes a
  runtime toggle for *whether wake-gated mode is active*, given the import
  succeeded. Operators who don't want the gate at all keep the env var
  unset.
- Multi-host config / per-Pi profiles. One DB = one Pi today.

## Design

### Bootstrap settings

15 new rows added to `bootstrap.py`'s seed table. The set:

| Key | Default | Type | Live-swap subscriber? |
|---|---|---|---|
| `agent.interaction_mode` | `continuous` | str (enum) | yes — restarts mic loop |
| `agent.trigger.phrase` | `"Welcome, traveler. Ask, and the cards shall reveal."` | str | yes |
| `agent.trigger.emotion` | `neutral` | str (enum) | yes |
| `agent.trigger.rounds` | `1` | int | yes |
| `agent.trigger.print_mode` | `ask` | str (enum) | yes |
| `agent.trigger.print_prompt` | `"Would you like a printed fortune?"` | str | yes |
| `agent.wakeword.enabled` | `false` | bool | yes — toggles `wake_detector` on RealMic |
| `agent.wakeword.model` | `hey_jarvis_v0.1` | str | yes — rebuilds detector |
| `agent.wakeword.threshold` | `0.5` | float | yes — mutates detector.threshold |
| `agent.input_device` | `auto` | str | yes — closes + reopens mic stream |
| `animator.head_lr.default` | `2063` | int | yes — already wired |
| `animator.head_ud.default` | `3082` | int | yes — already wired |
| `animator.eye.default` | `2045` | int | yes — already wired |
| `animator.jaw.default` | `1728` | int | yes — already wired |
| `animator.brow.default` | `2075` | int | yes — already wired |

Each row carries a `description` matching the existing prose style (one or
two sentences, why the operator cares). Servo descriptions include a "moves
the robot live" hint.

### Env-var → DB transition semantics

For the 10 keys that previously had env-var-only equivalents, the migration
respects the principle established by every prior live-config setting: **DB
is authoritative; env vars never override.** The transition:

- `bootstrap.py`'s `seed_default_settings` inserts the DB row only when
  absent. Existing rows are not overwritten.
- The agent's `__main__.py` keeps reading the env vars *only to seed
  `TriggerConfig.from_env`-style fallbacks for tests that don't run with a
  full DB*. In production with a real control snapshot, the env values are
  read once at construction, immediately overwritten by the snapshot, and
  no longer consulted.
- This matches how `agent.llm_model`, `agent.voice_model`, `agent.stt_backend`
  etc. already work — env defines startup defaults, DB takes over thereafter.

Net effect: an operator who has `LAFUFU_TRIGGER_ROUNDS=2` in their service
unit will get `rounds=2` for the first boot (via env), then the DB row
(seeded to `1`) overrides on the next snapshot. To preserve the env value,
they update the DB via the admin UI.

### Agent service subscribers

Six new live-swap handlers in `AgentService`:

- `_on_config_interaction_mode` — updates `self._interaction_mode`. If the
  current mic loop's mode differs, the loop's next iteration picks up the
  new mode at its next branch (mic loop already re-checks mode each
  iteration in `_mic_loop`). No restart needed.
- `_on_config_trigger_*` (5 handlers, one per trigger setting) — each
  mutates the corresponding field of `self._trigger` (a frozen dataclass →
  reassign with `dataclasses.replace`).
- `_on_config_wakeword_enabled` — toggles `self._mic.wake_detector` between
  the configured detector and `None`. Construction-time guard at startup
  decides whether the detector object exists; this just gates whether
  `wait_for_onset` uses it.
- `_on_config_wakeword_model` — constructs a fresh `OpenWakeWordDetector`
  with the new name and assigns it to `self._mic.wake_detector`. The
  wrapper has no `set_model` — `wakeword.py`'s init bakes `model_name` in
  at construction — so swap is the only path. Cost: one openwakeword
  model load (a few hundred ms via ONNX). Behavior matches the existing
  `_rebuild_stt` pattern.
- `_on_config_wakeword_threshold` — `self._mic.wake_detector.threshold = v`.
- `_on_config_input_device` — closes the existing PyAudio input stream and
  triggers `select_input_device` to re-pick on next `_ensure_stream()`. The
  device value `"auto"` falls through to the existing PREFER → PyAudio
  default → first-non-avoided chain; anything else is treated like the
  current `LAFUFU_INPUT_DEVICE` env (numeric index or name substring).

Trigger-mode mid-session guard: a `_on_config_interaction_mode` change
fires while a trigger session is mid-flight is rare but possible. The
handler updates the field but lets the active session complete; the next
`_mic_loop` iteration picks up the new mode. No mid-cycle abort.

### `select_input_device` change

The existing function gains one branch: when `LAFUFU_INPUT_DEVICE` is unset
*and* a control snapshot has populated `self._input_device_setting`
(distinct from the env), use that. Precedence:

1. `LAFUFU_INPUT_DEVICE` env (highest — operator override)
2. DB `agent.input_device` if non-`"auto"`
3. `PREFER` list match
4. PyAudio default
5. First non-AVOID (legacy fallback)

The `"auto"` sentinel skips step 2 and falls through to 3-5, matching the
existing zero-config behavior.

### New `/api/agent/input-devices` endpoint

```
GET /api/agent/input-devices
→ {
    "devices": [
      {"name": "auto",   "label": "auto — system default", "channels": 0},
      {"name": "1",      "label": "Microphone Array (Realtek)",  "channels": 4},
      {"name": "9",      "label": "Microphone Array (Realtek)",  "channels": 2},
      ...
    ]
  }
```

Enumerates PyAudio input devices (those with `maxInputChannels > 0`) using
the existing `get_pyaudio()` singleton. The first entry is always the
`"auto"` sentinel. The setting stores either `"auto"` or the device's
numeric index as a string (matching how `LAFUFU_INPUT_DEVICE` is parsed
today).

The frontend `DYNAMIC_OPTIONS["agent.input_device"]` calls this endpoint
and renders as a dropdown.

### Tab reorg

`categoryOf(key)` simplifies:

```py
prefix = key.split(".", 1)[0]
if key.startswith("speaker.") or key.startswith("tts."):
    return "audio"
return prefix if prefix in {"agent", "animator", "audio", "printer"} else "other"
```

Tabs: `agent | animator | audio | printer | other`. The current `model` tab
disappears — its entries (`agent.llm_model`, `agent.voice_model`,
`agent.stt_backend`, `agent.whisper_model`, `agent.system_prompt`) all
flow to `agent`. The current `audio` tab keeps its identity but loses its
agent.stt_* hangers-on (those go to `agent`).

This means **the agent tab gets crowded** — ~17 settings after this PR.
Mitigation: the existing search filter handles long lists fine, and
sub-categorization within the agent tab (group by `agent.trigger.*`,
`agent.wakeword.*`, etc.) is a future polish PR. For now the search bar +
sticky header carries it.

### Sticky tabs + search

`position: sticky; top: 0; z-index: 1` on the tab row + search input. The
scroll container is the settings panel's body. Verified pattern that
matches the existing `scroll-warm` chrome.

### `_rebuild_tts` leak fix

```py
def _rebuild_tts(self, reason: str) -> None:
    ...
    new_piper = self._piper_factory(...)
    new_player = self._player_factory(new_piper.sample_rate) if self._player_factory else None
    # Close the outgoing player before reassigning — _PyAudioPlayer holds
    # a pyaudio.PyAudio() instance + open WASAPI stream that don't get
    # GC'd deterministically.
    old_player = self._speaker_play
    self._piper = new_piper
    self._speaker_play = new_player
    if hasattr(old_player, "close"):
        try:
            old_player.close()
        except Exception as e:
            self.log.warning("speaker_play.close.failed.during_swap error=%s", e)
    ...
```

The hasattr guard is no-op for `_AplayPlayer` and `_NoOpPlayer` (neither
implements `close`), so the Pi path is unchanged.

### Front-end widget shapes

The form already accepts `OptionEntry[]` (string or `{value, label}`) from
PR #14. New options:

- `agent.interaction_mode` → `["continuous", "trigger"]`
- `agent.trigger.emotion` → `["happy", "sad", "angry", "surprised",
  "neutral", "agree", "disagree"]` (mirror of `Emotion` literal)
- `agent.trigger.print_mode` → `["none", "auto", "ask"]`
- `agent.wakeword.model` → hardcoded list of openwakeword's default models
  (`hey_jarvis_v0.1`, `alexa_v0.1`, `hey_mycroft_v0.1`, `hey_rhasspy_v0.1`,
  `timer_v0.1`, `weather_v0.1`). A future PR could enumerate from disk like
  the voices endpoint once custom wake-word training (Maven's box) lands.
- `agent.input_device` → fetched from `/api/agent/input-devices`.

New `SETTING_RANGES` entries (slider widgets) for:

- `agent.trigger.rounds` — `{min: 1, max: 10, step: 1}`
- `agent.wakeword.threshold` — `{min: 0.0, max: 1.0, step: 0.05}`
- `animator.head_lr.default` — `{min: 1828, max: 2298, step: 1}` (matches `SERVO_RANGES` in `frames_section.tsx`)
- `animator.head_ud.default` — `{min: 2885, max: 3278, step: 1}`
- `animator.eye.default` — `{min: 1500, max: 2500, step: 1}` (approximate; pulled from `pose.clamp_dxl`)
- `animator.jaw.default` — `{min: 1500, max: 2500, step: 1}`
- `animator.brow.default` — `{min: 1500, max: 2500, step: 1}`

The implementation plan extracts the canonical servo ranges from
`pose.clamp_dxl` so the form and the animator agree.

## Data flow

```
admin browser (any device) opens settings tab
  → GET /api/settings → rows incl. new agent.* / animator.*
  → GET /api/agent/{voices, whisper-models, stt-backends, input-devices,
                   models} → dropdown options + cached/size hints
  → operator slides agent.wakeword.threshold from 0.5 to 0.4
  → PUT /api/settings/agent.wakeword.threshold (value=0.4)
  → control publishes config.changed.agent.wakeword.threshold
  → agent's _on_config_wakeword_threshold handler updates
     self._mic.wake_detector.threshold = 0.4
  → next wake-listen iteration uses the new threshold
```

Voice-swap leak path:

```
operator switches voice in admin UI
  → PUT /api/settings/agent.voice_model
  → control → config.changed.agent.voice_model
  → agent._on_config_voice_model → _rebuild_tts
  → _rebuild_tts captures old_player, swaps fields,
     then old_player.close() (new — fixes the leak)
  → next agent.speak uses the new piper + new player
```

## Error handling

- **Bad enum value via API**: the existing settings router validates by
  `value_type` only; enum validation lives in the agent's `_on_config_*`
  handlers (refuse + log warning, retain previous value). Matches how
  `LAFUFU_TRIGGER_EMOTION` already validates in `trigger.py:from_env`.
- **DB row missing at agent startup** (fresh DB before
  `seed_default_settings` runs): `TriggerConfig.from_env` fallback supplies
  the env values, exactly as today. The snapshot replay overwrites once
  control has booted.
- **Input device disappears mid-session** (mic unplugged): PyAudio's
  `_ensure_stream` already catches `OSError` and falls through to
  re-selection — covered by the existing handling.
- **Wakeword threshold out of range** (e.g. `1.5`): the slider clamps in
  the form; the agent's handler additionally clamps to `[0.0, 1.0]` as
  belt-and-braces.

## Testing

- 9 new live-swap integration tests in `test_service.py`, one per agent
  setting — assert the agent attribute mutates and (where applicable) the
  next mic loop iteration uses the new value. Mirror
  `test_voice_model_change_swaps_piper_instance`'s structure.
- Pure unit test for the new `categoryOf` in a new
  `web/tests/settings_form.test.ts` — assert each existing seeded key maps
  to the expected tab. Future settings get caught when added without a
  category update.
- Pure unit test for `_rebuild_tts` close — fake `_player_factory` returns
  a player with a `close()` mock; assert the old player's close is called
  exactly once on swap.
- Existing 333 pytest + 30 vitest must stay green.
- No on-device test needed for the migration mechanics; the wake/trigger
  flow is unchanged once the DB rows are seeded.

## Files

Create:

- `packages/control/src/lafufu_control/api/routers/agent.py` (modify) — new
  `GET /api/agent/input-devices` endpoint.
- `web/tests/settings_form.test.ts` — `categoryOf` coverage.

Modify:

- `packages/control/src/lafufu_control/bootstrap.py` — 15 new seed rows.
- `packages/agent/src/lafufu_agent/service.py` — 9 new subscribers in
  `on_startup`, 9 new `_on_config_*` handler methods. `_rebuild_tts` close
  fix. The existing `TriggerConfig` snapshot path adapts to receive live
  field updates.
- `packages/agent/src/lafufu_agent/__main__.py` — `_PyAudioPlayer` already
  in place from PR #14; this only adds wiring for the input-device
  re-select after a settings change.
- `packages/agent/src/lafufu_agent/audio_capture.py` — `select_input_device`
  precedence chain gains the DB-snapshot branch (step 2 above).
- `web/src/admin/settings_form.tsx` — `categoryOf` reorg, sticky header,
  new `DYNAMIC_OPTIONS` + `SETTING_RANGES` entries.
- `web/src/shared/api.ts` — `listInputDevices()` method.
- `packages/agent/tests/test_service.py` — 9 + 1 new tests.
- `docs/local-dev.md` — note the env-var fallback semantics.
