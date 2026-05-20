# Drag-to-puppeteer the lafufu on `/pet` — Design

- **Date:** 2026-05-19
- **Status:** Approved (design) — pending written-spec review
- **Topic:** Drag controls for the 3D lafufu + idle-animation on/off toggle

## Context

`/pet` is the mobile "Tamagotchi" page. It already renders a procedural,
fully-rigged 3D labubu face (`web/src/pet/pet_scene.ts`) and already mirrors the
live hardware pose by subscribing to the `animator.pose` NATS topic.

Today `/pet`'s pointer drag is **view-only**: it accumulates temporary
`yaw`/`pitch` offsets that decay back to zero (`yaw *= 0.985`) and never touches
the servos.

The animator service (`packages/animator/src/lafufu_animator/service.py`) drives
five DXL servos. It runs an idle-animation loop (subtle "living presence"
motion) that defers for 1.5 s after any user intent (`INTENT_QUIET_S`) and can
be disabled entirely via the **already-existing** setting
`animator.idle_animation.enabled` (registered in `bootstrap.py:64`, honored at
`service.py:63` and `service.py:423`). That setting is editable today only in
the admin Settings → "other" tab — there is no convenient place to flip it
while interacting with the lafufu.

## Problem

Two gaps:

1. Dragging the 3D lafufu does nothing to the physical robot. We want drag to
   **puppeteer** the head — map up/down/left/right onto the `head_lr` /
   `head_ud` servos.
2. There is no convenient on/off control for the idle animation. Whether the
   head springs back or holds after a drag should be governed by this toggle,
   and it should live where the user is actually puppeteering.

## Goals

- On `/pet`, dragging the 3D lafufu's head commands the `head_lr` / `head_ud`
  servos in real time, with the 3D model tracking the drag instantly.
- A toggle on `/pet`, bound to `animator.idle_animation.enabled`, turns the
  animator's idle motion on/off.
- Release behavior follows the toggle, with **no backend changes**:
  - **Idle ON** → after the drag stops, the 1.5 s quiet window elapses and the
    idle loop resumes — the head drifts back to living-presence motion.
  - **Idle OFF** → the idle loop is disabled, so the animator's `_target_pose`
    holds the last commanded position — the head stays where you left it.

## Non-goals (deliberately out of scope)

- The `/face` kiosk page — stays as-is (looping `lafufu-bg.mp4`).
- Dragging the eye / jaw / brow servos — only the two head axes for this
  milestone ("start by mapping up/down left/right").
- Suppressing agent-driven emotion **expressions** — the toggle covers the idle
  loop only. No backend flag for expressions exists and none is added here.
- Swapping in the `assets/Face Shell.obj` sculpt. That model is a 21.7 MB,
  ~500k-face, unrigged static shell; using it needs an offline decimation +
  Draco-glTF conversion step. The drag plumbing is kept model-agnostic so the
  sculpt can drop in later, but it is not done now.

## Decisions

Captured during brainstorming:

- **Drag behavior:** drag moves both the on-screen model *and* the physical
  servos.
- **Model:** reuse the existing procedural rigged model on `/pet`; keep the
  drag→servo plumbing model-agnostic.
- **Page:** `/pet` (not `/face`).
- **Drag→servo approach:** **A — absolute aim.** Drag aims the head at an
  absolute `head_lr`/`head_ud` target; the model updates optimistically; reuse
  the throttle + grace-window patterns proven in
  `web/src/admin/body_panel.tsx`.
- **Toggle scope:** idle animation only — bound to the existing
  `animator.idle_animation.enabled` setting.

## Architecture

**Files touched**

- `web/src/pet/pet.tsx` — drag logic + toggle UI (the only substantive change).
- A small pure helper for the pixel→DXL mapping (co-located in `pet.tsx` or a
  tiny module) + its vitest test under `web/tests`.
- No backend changes. No `pet_scene.ts` changes — its `setPose` already maps
  DXL values onto the rig.

### Component 1 — Drag puppeteers the head

Replaces `/pet`'s view-only decay-drag with absolute-aim servo control.

**State** (within the `Pet` component):

- `lastPose: Record<string, number>` — latest `animator.pose` payload, kept so a
  fresh drag can "grab" the head at its current position.
- `headLr`, `headUd` — commanded DXL targets while puppeteering.
- `gesture: "none" | "puppeteer" | "tug"` — decided at pointer-down.
- `lastHeadDragTs: number` — timestamp for the post-release grace window.

**Pointer flow**

- **down:** `pickZone()` (raycast against `hitGroup`). Ear zone → `gesture =
  "tug"` (existing ear-tug path, unchanged). Otherwise → `gesture =
  "puppeteer"`: seed `headLr`/`headUd` from `lastPose` (fall back to each
  servo's range midpoint).
- **move (puppeteer):** convert pixel delta to DXL delta —
  `headLr += dx * LR_PER_PX`, `headUd += dy * UD_PER_PX` — and clamp to
  `SERVO_RANGES` (`head_lr: [1828, 2298]`, `head_ud: [2885, 3278]`). Push
  optimistically to the model via
  `api3d.setPose({ head_lr: headLr, head_ud: headUd })`. Send
  `api.animatorPreview("head_lr", …)` and `("head_ud", …)`, throttled ~40 ms
  (one shared timer that flushes both axes' latest values — adapted from
  `body_panel.tsx`'s `pendingPreview` debounce).
- **up:** stamp `lastHeadDragTs`; stop. A tap (no drag, held < 350 ms) still
  runs the existing pat / poke / tickle zone logic.

**Direction:** drag right → head turns to its right; drag down → head tilts
down. Final sign and `*_PER_PX` sensitivity are tuned against the rig during
implementation; a sensible start is full range across ~60% of the canvas
width/height (`LR_PER_PX ≈ 470 / 500`, `UD_PER_PX ≈ 393 / 500`).

**Feedback-loop fix.** The `animator.pose` subscription keeps driving the model,
but while `gesture === "puppeteer"` *or* within 800 ms of `lastHeadDragTs`, it
drops `head_lr` / `head_ud` from the incoming payload before calling `setPose`
(the grace-window idea from `body_panel.tsx`'s `effective()`). Eye / jaw / brow
keep flowing live the entire time. After the grace window the head resumes
following `animator.pose` — which is exactly correct: idle-on shows the drift,
idle-off reports the held position so the model holds too.

**No 1 s re-ping** (unlike `body_panel.tsx`): `body_panel` re-pings because it
holds a value *while idle stays enabled*. Here, idle-on means we *want* the
release; idle-off disables the idle loop entirely, so the animator's
`_target_pose` holds with no re-ping. The toggle does the work.

**Expression interaction.** Each `animator.intent.preview` clears the animator's
`_current_expression` (`service.py:299`), so grabbing the head cancels a playing
emotion gesture. This is expected for manual control and is left as-is.

**Easter-egg reconciliation.** The "spin it around" egg relied on unbounded
accumulated `yaw`, which absolute-aim clamping removes. Rework it to fire on
rapid left↔right sweeps — 3 direction reversals near the `head_lr` range
extremes within ~1.5 s. Pat / ear-tug / poke / shake are unchanged. The ear-tug
still works because the gesture is decided by the zone at pointer-down.

**Removed:** the `blend()` rAF loop and the `yaw` / `pitch` decay state. `velY`
(for ear-tug) and `didDrag` (tap vs. drag discrimination) are retained.

### Component 2 — Idle-animation toggle on `/pet`

A toggle bound to the existing `animator.idle_animation.enabled` setting.

- **Read on mount:** from `api.snapshot()` — its `settings` array carries every
  key; find `animator.idle_animation.enabled` and parse the bool ("true"/"1").
- **Write on toggle:** `api.patchSetting("animator.idle_animation.enabled",
  { value: <bool>, value_type: "bool" })`. The settings router persists it and
  publishes `config.changed.animator.idle_animation.enabled`; the animator
  applies it live via its config-changed subscription.
- **Stay in sync:** subscribe to `config.changed.animator.idle_animation.enabled`
  over the existing NATS-ws bridge (the bridge supports concrete-subject
  subscriptions) so a change made in the admin UI — or in the future Body panel
  — reflects on `/pet`.
- **Placement:** a toggle chip in `/pet`'s existing bottom action-chip row
  (alongside `open chat` / `enable shake` / `hint`), labeled `idle: on` /
  `idle: off`. The click flips state optimistically and is reconciled by the
  `config.changed` echo; a failed `patchSetting` shows a toast and reverts.

**UX:** idle **off** makes `/pet` a stable puppeteering surface — the head holds
wherever you drag it. Idle **on** makes drag a "nudge" — the head drifts back to
living-presence motion after ~1.5 s.

## Data flow

```
pointer drag ─▶ pixel→DXL map ─▶ headLr/headUd (clamped)
                     │                  │
                     │                  ├─▶ api3d.setPose()         (instant visual)
                     │                  └─▶ api.animatorPreview()   (~40ms throttle)
                     │                            │
                     │                   POST /api/animator/preview
                     │                            │
                     │                   NATS animator.intent.preview
                     │                            │
                     │                   AnimatorService → DXL bus
                     │                            │
                     ▼                   NATS animator.pose (20 Hz)
        animator.pose subscription ◀─────────────┘
          (drops head_lr/head_ud while puppeteering + 800ms grace)

toggle click ─▶ api.patchSetting(idle_animation.enabled)
                     │
            POST /api/settings/... ─▶ NATS config.changed.animator.idle_animation.enabled
                     │                          │
            /pet subscription ◀────────────────┴──▶ AnimatorService (enables/disables idle loop)
```

## Edge cases & error handling

- **`api3d` not yet created** when a pose frame or pointer event arrives —
  guard with the existing `api3d?.` optional chaining.
- **`animatorPreview` request fails** — swallow per-call (the visual already
  moved); matches `body_panel.tsx`'s `.catch(() => {})`.
- **`snapshot()` fails on mount** — default the toggle to `on` (the setting's
  factory default) and let the `config.changed` subscription correct it.
- **`patchSetting` fails** — revert the optimistic toggle state and surface a
  toast.
- **Pointer-up off the canvas** — `setPointerCapture` (already used) keeps
  events flowing; `onPointerCancel` is wired to the same handler as
  `onPointerUp`.
- **Stale closures on navigation** — every NATS subscription is pushed to the
  `subs` array and drained in `onCleanup`, per the existing pattern.

## Testing

- **Unit (vitest, `web/tests`):** the pure pixel→DXL mapping helper — verify
  clamping at both ends of `head_lr` and `head_ud` ranges, and the
  delta-accumulation sign.
- **Manual:** run the stack with the fake DXL bus; drag on `/pet` and confirm
  `animator.intent.preview` messages for `head_lr` and `head_ud`; toggle idle
  **off** → head holds the dragged position; toggle idle **on** → head drifts
  back after ~1.5 s; verify the toggle reflects a change made from the admin
  Settings tab.

## Related / future work (not in this spec)

- **Body panel → "complete animation builder" rework.** A separate effort will
  rebuild `web/src/admin/body_panel.tsx` into a full animation builder and will
  include its own idle-animation toggle. Because it binds the *same*
  `animator.idle_animation.enabled` setting, it stays consistent with the `/pet`
  toggle automatically. That rework deserves its own brainstorm/spec.
- **`Face Shell.obj` sculpt.** Decimating and converting the ZBrush sculpt to a
  Draco-compressed glTF, and deciding whether to overlay the procedural rig on
  it for articulation — a future spec.
- **Extending drag to eye / jaw / brow** — a later milestone.
