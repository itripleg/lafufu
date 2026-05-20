# Lafufu Drag Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dragging the 3D lafufu on `/pet` command the physical `head_lr`/`head_ud` servos in real time, and add an on-page toggle for the animator's idle animation.

**Architecture:** All changes are frontend, in `web/src/pet/`. A new pure helper module (`head_drag.ts`) maps pixel drag deltas to clamped DXL servo values and is unit-tested. `pet.tsx`'s pointer interaction is reworked from view-only decay-drag to absolute-aim servo puppeteering, reusing the throttle + grace-window patterns from `web/src/admin/body_panel.tsx`. A toggle chip bound to the existing `animator.idle_animation.enabled` setting governs whether the head drifts back to idle or holds. No backend changes.

**Tech Stack:** SolidJS, TypeScript (strict, `noUnusedLocals`/`noUnusedParameters`), three.js, vitest (jsdom), NATS-over-WebSocket.

**Spec:** `docs/superpowers/specs/2026-05-19-lafufu-drag-controls-design.md`

---

## File Structure

- **Create** `web/src/pet/head_drag.ts` — pure helpers: pixel→DXL mapping, clamping, axis midpoints. No three.js objects, no DOM. Imports only `SERVO_RANGES` from `pet_scene.ts`.
- **Create** `web/tests/head_drag.test.ts` — vitest unit tests for `head_drag.ts`.
- **Modify** `web/src/pet/pet.tsx` — rework pointer interaction (Task 2) and add the idle-animation toggle (Task 3).

No backend files, no `pet_scene.ts` changes.

## Conventions for this plan

- All `npm`/`npx` commands run **from the `web/` directory** unless stated otherwise.
- Typecheck command: `npx tsc --noEmit` (the full `npm run build` also writes a static bundle into `packages/control` — not wanted for a check).
- Tests: `npx vitest run`.
- Manual verification steps are marked **(requires the running stack)** — control + animator + NATS reachable via the dev proxy. If the stack is unavailable, the typecheck and unit tests are the completion gate; note in the commit that manual verification was deferred.

---

## Task 1: Pixel→DXL mapping helper

**Files:**
- Create: `web/src/pet/head_drag.ts`
- Test: `web/tests/head_drag.test.ts`

- [ ] **Step 1: Write the failing test**

Create `web/tests/head_drag.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { applyDragDelta, axisMid, clamp } from "../src/pet/head_drag";
import { SERVO_RANGES } from "../src/pet/pet_scene";

describe("clamp", () => {
  it("returns the value when within range", () => {
    expect(clamp(5, 0, 10)).toBe(5);
  });
  it("clamps below the low bound", () => {
    expect(clamp(-3, 0, 10)).toBe(0);
  });
  it("clamps above the high bound", () => {
    expect(clamp(99, 0, 10)).toBe(10);
  });
});

describe("axisMid", () => {
  it("returns the midpoint of each head servo range", () => {
    expect(axisMid("head_lr")).toBe(
      (SERVO_RANGES.head_lr[0] + SERVO_RANGES.head_lr[1]) / 2,
    );
    expect(axisMid("head_ud")).toBe(
      (SERVO_RANGES.head_ud[0] + SERVO_RANGES.head_ud[1]) / 2,
    );
  });
});

describe("applyDragDelta", () => {
  it("a positive delta increases the value", () => {
    const mid = axisMid("head_lr");
    expect(applyDragDelta("head_lr", mid, 50)).toBeGreaterThan(mid);
  });
  it("a negative delta decreases the value", () => {
    const mid = axisMid("head_ud");
    expect(applyDragDelta("head_ud", mid, -50)).toBeLessThan(mid);
  });
  it("clamps at the high end of head_lr", () => {
    expect(applyDragDelta("head_lr", SERVO_RANGES.head_lr[1], 10000)).toBe(
      SERVO_RANGES.head_lr[1],
    );
  });
  it("clamps at the low end of head_ud", () => {
    expect(applyDragDelta("head_ud", SERVO_RANGES.head_ud[0], -10000)).toBe(
      SERVO_RANGES.head_ud[0],
    );
  });
  it("never returns a value outside the axis range", () => {
    const [lo, hi] = SERVO_RANGES.head_lr;
    for (const delta of [-9999, -100, 0, 100, 9999]) {
      const out = applyDragDelta("head_lr", axisMid("head_lr"), delta);
      expect(out).toBeGreaterThanOrEqual(lo);
      expect(out).toBeLessThanOrEqual(hi);
    }
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `web/`): `npx vitest run head_drag`
Expected: FAIL — `Failed to resolve import "../src/pet/head_drag"` (the module does not exist yet).

- [ ] **Step 3: Write the helper module**

Create `web/src/pet/head_drag.ts`:

```ts
/**
 * Pure helpers for drag → head-servo mapping on the /pet page.
 *
 * Keeps the pixel-delta → DXL-position math out of the SolidJS component so it
 * can be unit-tested without three.js or the DOM. `SERVO_RANGES` is the single
 * source of truth for the servo bounds — imported, not duplicated.
 */
import { SERVO_RANGES } from "./pet_scene";

/** The two head servos this milestone maps drag onto. */
export type HeadAxis = "head_lr" | "head_ud";

/**
 * DXL units travelled per pixel of drag, per axis. Tuned so a ~500px drag
 * spans the servo's full range — a comfortable swipe across a phone screen.
 */
export const LR_PER_PX =
  (SERVO_RANGES.head_lr[1] - SERVO_RANGES.head_lr[0]) / 500;
export const UD_PER_PX =
  (SERVO_RANGES.head_ud[1] - SERVO_RANGES.head_ud[0]) / 500;

/** Clamp `v` into the inclusive `[lo, hi]` range. */
export const clamp = (v: number, lo: number, hi: number): number =>
  Math.max(lo, Math.min(hi, v));

/** Midpoint of an axis range — the seed used before any live pose is known. */
export const axisMid = (axis: HeadAxis): number => {
  const [lo, hi] = SERVO_RANGES[axis];
  return (lo + hi) / 2;
};

/**
 * Apply a pixel drag delta to a current DXL value, clamped to the axis range.
 * Sign is caller-controlled: pass `dx`/`dy` (or their negation) so the head
 * turns the intended way for the rig.
 */
export const applyDragDelta = (
  axis: HeadAxis,
  currentDxl: number,
  deltaPx: number,
): number => {
  const [lo, hi] = SERVO_RANGES[axis];
  const perPx = axis === "head_lr" ? LR_PER_PX : UD_PER_PX;
  return clamp(currentDxl + deltaPx * perPx, lo, hi);
};
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `web/`): `npx vitest run head_drag`
Expected: PASS — all assertions in `head_drag.test.ts` green.

- [ ] **Step 5: Commit**

```bash
git add web/src/pet/head_drag.ts web/tests/head_drag.test.ts
git commit -m "$(cat <<'EOF'
feat(pet): pure pixel->DXL head-drag mapping helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Rework /pet pointer interaction for head puppeteering

Replaces `/pet`'s view-only decay-drag with absolute-aim servo puppeteering. Decides the gesture (puppeteer vs. ear-tug) at pointer-down, sends throttled `animatorPreview` calls during a puppeteer drag, updates the model optimistically, suppresses the `animator.pose` echo for the head during/just-after a drag, and reworks the "spin" easter egg to use sweep reversals (clamping removed the unbounded yaw it relied on).

**Files:**
- Modify: `web/src/pet/pet.tsx`

- [ ] **Step 1: Add the head_drag import**

In `web/src/pet/pet.tsx`, the import block currently ends at line 8 with:

```ts
import { createPetScene, SERVO_RANGES } from "./pet_scene";
```

Add this line immediately after it:

```ts
import { applyDragDelta, axisMid } from "./head_drag";
```

- [ ] **Step 2: Replace the pointer-interaction state block**

Find this block (currently lines 69–92):

```ts
  // ---- Pointer interaction: drag rotates head; tap detects zones ----------
  let dragging = false;
  let didDrag = false;
  let lastX = 0, lastY = 0;
  let downX = 0, downY = 0;
  let downT = 0;
  let yaw = 0, pitch = 0; // user-applied offsets on top of servo pose
  let velY = 0;            // last drag dy — used to detect downward "tug" gestures
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  const updateRaycaster = (clientX: number, clientY: number) => {
    const rect = host.getBoundingClientRect();
    ndc.x =  ((clientX - rect.left) / rect.width)  * 2 - 1;
    ndc.y = -((clientY - rect.top)  / rect.height) * 2 + 1;
    if (api3d) raycaster.setFromCamera(ndc, api3d.camera);
  };

  const pickZone = (clientX: number, clientY: number): string | null => {
    if (!api3d) return null;
    updateRaycaster(clientX, clientY);
    const hits = raycaster.intersectObjects(api3d.hitGroup.children, false);
    return hits.length ? (hits[0].object.userData.zone as string) : null;
  };
```

Replace the whole block with:

```ts
  // ---- Pointer interaction: drag puppeteers the head; tap detects zones ----
  let dragging = false;
  let didDrag = false;
  let gesture: "none" | "puppeteer" | "tug" = "none";
  let tugSide: "L" | "R" | null = null;
  let lastX = 0, lastY = 0;
  let downX = 0, downY = 0;
  let downT = 0;
  let velY = 0;            // last drag dy — used to detect downward "tug" gestures

  // Commanded head-servo targets (DXL units) while puppeteering.
  let headLr = axisMid("head_lr");
  let headUd = axisMid("head_ud");
  let lastHeadDragTs = 0;                       // post-release grace window
  let lastPose: Record<string, number> = {};   // latest animator.pose payload

  // Spin easter-egg: timestamps of recent left<->right reversals near a
  // range extreme. Replaces the old unbounded-yaw detector.
  let sweepReversals: number[] = [];
  let sweepDir = 0;        // -1 / +1 — last horizontal drag direction

  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  // True while the user owns the head: actively puppeteering, or within 800ms
  // of release. The animator.pose echo is suppressed for head_lr/head_ud
  // during this window so the servo round-trip can't fight the drag.
  const headControlActive = () =>
    gesture === "puppeteer" || performance.now() - lastHeadDragTs < 800;

  const updateRaycaster = (clientX: number, clientY: number) => {
    const rect = host.getBoundingClientRect();
    ndc.x =  ((clientX - rect.left) / rect.width)  * 2 - 1;
    ndc.y = -((clientY - rect.top)  / rect.height) * 2 + 1;
    if (api3d) raycaster.setFromCamera(ndc, api3d.camera);
  };

  const pickZone = (clientX: number, clientY: number): string | null => {
    if (!api3d) return null;
    updateRaycaster(clientX, clientY);
    const hits = raycaster.intersectObjects(api3d.hitGroup.children, false);
    return hits.length ? (hits[0].object.userData.zone as string) : null;
  };

  // Throttled servo command — sends the latest headLr/headUd at most ~every
  // 40ms. A throttle (not body_panel's trailing debounce) so the servos keep
  // tracking during a continuous drag, not only when the finger pauses.
  let previewTimer: ReturnType<typeof setTimeout> | undefined;
  const flushPreview = () => {
    previewTimer = undefined;
    api.animatorPreview("head_lr", Math.round(headLr)).catch(() => {});
    api.animatorPreview("head_ud", Math.round(headUd)).catch(() => {});
  };
  const schedulePreview = () => {
    if (previewTimer === undefined) previewTimer = setTimeout(flushPreview, 40);
  };

  // Count a reversal when the horizontal drag flips direction while the head
  // is near a head_lr extreme. 3 within 1.5s trips the "spin" easter egg.
  const trackSweep = (dx: number) => {
    if (Math.abs(dx) < 2) return;
    const dir = dx > 0 ? 1 : -1;
    const [lo, hi] = SERVO_RANGES.head_lr;
    const span = hi - lo;
    const nearEnd = headLr <= lo + span * 0.12 || headLr >= hi - span * 0.12;
    if (sweepDir !== 0 && dir !== sweepDir && nearEnd) {
      const now = performance.now();
      sweepReversals = sweepReversals.filter((t) => now - t < 1500);
      sweepReversals.push(now);
    }
    sweepDir = dir;
  };
```

- [ ] **Step 3: Replace `onPointerDown`**

Find (currently lines 94–100):

```ts
  const onPointerDown = (e: PointerEvent) => {
    dragging = true;
    didDrag = false;
    lastX = e.clientX; lastY = e.clientY;
    downX = e.clientX; downY = e.clientY; downT = performance.now();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };
```

Replace with:

```ts
  const onPointerDown = (e: PointerEvent) => {
    dragging = true;
    didDrag = false;
    lastX = e.clientX; lastY = e.clientY;
    downX = e.clientX; downY = e.clientY; downT = performance.now();
    velY = 0;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);

    // The zone under the initial press decides the gesture for this drag.
    const zone = pickZone(e.clientX, e.clientY);
    if (zone === "earL" || zone === "earR") {
      gesture = "tug";
      tugSide = zone === "earL" ? "L" : "R";
    } else {
      gesture = "puppeteer";
      tugSide = null;
      // Grab the head wherever it currently is.
      headLr = lastPose.head_lr ?? axisMid("head_lr");
      headUd = lastPose.head_ud ?? axisMid("head_ud");
      sweepReversals = [];
      sweepDir = 0;
    }
  };
```

- [ ] **Step 4: Replace `onPointerMove`**

Find (currently lines 101–112):

```ts
  const onPointerMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) didDrag = true;
    lastX = e.clientX; lastY = e.clientY;
    yaw   += dx * 0.006;
    pitch += dy * 0.006;
    velY = dy;
    void dx;
    // Clamp pitch so we never look behind ourselves.
    pitch = Math.max(-0.7, Math.min(0.7, pitch));
  };
```

Replace with:

```ts
  const onPointerMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) didDrag = true;
    lastX = e.clientX; lastY = e.clientY;
    velY = dy;

    if (gesture === "puppeteer") {
      // Drag right -> head turns right; drag down -> head tilts down. If the
      // rig moves the wrong way during manual verification, negate dx/dy here.
      headLr = applyDragDelta("head_lr", headLr, dx);
      headUd = applyDragDelta("head_ud", headUd, dy);
      lastHeadDragTs = performance.now();
      // Optimistic visual update — the model tracks the drag immediately.
      api3d?.setPose({ head_lr: headLr, head_ud: headUd });
      schedulePreview();
      trackSweep(dx);
    }
  };
```

- [ ] **Step 5: Replace `onPointerUp`**

Find (currently lines 113–135):

```ts
  const onPointerUp = (e: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    const heldMs = performance.now() - downT;

    if (!didDrag && heldMs < 350) {
      // Tap — check zone.
      const zone = pickZone(e.clientX, e.clientY);
      if (zone) handleTap(zone, e.clientX, e.clientY);
    } else {
      // Drag end — was it a "tug" on an ear?
      const zone = pickZone(downX, downY);
      if (zone === "earL" && velY > 6) { markFound("tugL"); api3d?.wobbleEar("L"); triggerExpression("surprised"); flashHint(e.clientX, e.clientY, "boi-oing!"); }
      if (zone === "earR" && velY > 6) { markFound("tugR"); api3d?.wobbleEar("R"); triggerExpression("surprised"); flashHint(e.clientX, e.clientY, "boi-oing!"); }
      // Big spin?
      if (Math.abs(yaw) > Math.PI * 1.4) {
        markFound("spin");
        triggerExpression("disagree");
        flashHint(e.clientX, e.clientY, "stop spinning me!");
        yaw = yaw % (Math.PI * 2);
      }
    }
  };
```

Replace with:

```ts
  const onPointerUp = (e: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    const heldMs = performance.now() - downT;
    const wasGesture = gesture;
    gesture = "none";

    if (!didDrag && heldMs < 350) {
      // Tap — check zone (pat / poke / tickle).
      const zone = pickZone(e.clientX, e.clientY);
      if (zone) handleTap(zone, e.clientX, e.clientY);
      return;
    }

    if (wasGesture === "tug") {
      // Drag that started on an ear, flicked downward — "tug" easter egg.
      if (tugSide && velY > 6) {
        markFound(tugSide === "L" ? "tugL" : "tugR");
        api3d?.wobbleEar(tugSide);
        triggerExpression("surprised");
        flashHint(e.clientX, e.clientY, "boi-oing!");
      }
    } else if (wasGesture === "puppeteer") {
      // Commit the exact release position promptly.
      if (previewTimer !== undefined) {
        clearTimeout(previewTimer);
        previewTimer = undefined;
      }
      flushPreview();
      // "Spin" easter egg — 3 rapid end-to-end reversals.
      if (sweepReversals.length >= 3) {
        sweepReversals = [];
        markFound("spin");
        triggerExpression("disagree");
        flashHint(e.clientX, e.clientY, "stop spinning me!");
      }
    }
  };
```

- [ ] **Step 6: Update the `animator.pose` subscription**

Find (currently lines 204–206, inside `onMount`):

```ts
    subs.push(nats.subscribe("animator.pose", (f) => {
      api3d?.setPose(f.payload);
    }));
```

Replace with:

```ts
    subs.push(nats.subscribe("animator.pose", (f) => {
      lastPose = f.payload;
      // While the user owns the head, drop the head axes from the echo so the
      // servo round-trip can't fight the drag. Eyes/jaw/brow keep flowing.
      const p = { ...f.payload };
      if (headControlActive()) {
        delete p.head_lr;
        delete p.head_ud;
      }
      api3d?.setPose(p);
    }));
```

- [ ] **Step 7: Remove the blend rAF loop**

Find (currently lines 209–222, inside `onMount`):

```ts
    // Combine raw user drag with the latest servo target so the user can
    // override / overlay on top of the live pose.
    let frame: number | undefined;
    const blend = () => {
      if (api3d) {
        // Decay user yaw toward 0 so it gently rests back to the servo pose.
        yaw   *= 0.985;
        pitch *= 0.985;
        api3d.head.rotation.y += yaw   * 0.04;
        api3d.head.rotation.x += pitch * 0.04;
      }
      frame = requestAnimationFrame(blend);
    };
    frame = requestAnimationFrame(blend);
```

Delete this entire block. The model is now driven directly by `setPose` (the optimistic update in `onPointerMove` and the `animator.pose` echo), so the blend loop is no longer needed.

- [ ] **Step 8: Update the in-`onMount` cleanup**

Find (currently lines 227–230):

```ts
    onCleanup(() => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("devicemotion", onMotion);
    });
```

Replace with:

```ts
    onCleanup(() => {
      if (previewTimer !== undefined) clearTimeout(previewTimer);
      window.removeEventListener("devicemotion", onMotion);
    });
```

- [ ] **Step 9: Remove the now-unused `void SERVO_RANGES` line**

Find (currently lines 525–527, at the end of the file):

```ts
export default Pet;
// keep import to satisfy unused-import linters if SERVO_RANGES is added later
void SERVO_RANGES;
```

Replace with:

```ts
export default Pet;
```

`SERVO_RANGES` is now used by `trackSweep`, so the placeholder is no longer needed.

- [ ] **Step 10: Typecheck**

Run (from `web/`): `npx tsc --noEmit`
Expected: no errors. (If `tsc` reports an unused `yaw`/`pitch`/`frame`, a Step 2/7/8 replacement was missed — re-check that block.)

- [ ] **Step 11: Run the unit tests**

Run (from `web/`): `npx vitest run`
Expected: PASS — `head_drag.test.ts`, `design.test.ts`, `nats_ws.test.ts` all green.

- [ ] **Step 12: Manual verification (requires the running stack)**

From `web/`: `npm run dev`, open `/pet` on a device/emulator. With the animator running:
- Drag across the face → the 3D head turns; the animator log shows `animator.intent.preview` for `head_lr` and `head_ud`.
- Drag right → head turns right; drag down → head tilts down. If reversed, negate `dx`/`dy` in the `onPointerMove` `applyDragDelta` calls (Step 4).
- Tap the head 3× → "pat" easter egg still fires; tug an ear downward → "boi-oing!" still fires.
- Sweep left↔right end-to-end ~3× quickly → "stop spinning me!" fires.

If the stack is unavailable, Steps 10–11 are the completion gate.

- [ ] **Step 13: Commit**

```bash
git add web/src/pet/pet.tsx
git commit -m "$(cat <<'EOF'
feat(pet): drag puppeteers the head_lr/head_ud servos

Replaces /pet's view-only decay-drag with absolute-aim servo control:
gesture decided at pointer-down, throttled animatorPreview during a
puppeteer drag, optimistic model update, and an 800ms grace window that
suppresses the animator.pose echo for the head axes. The "spin" easter
egg is reworked to sweep-reversal detection since clamping removes the
unbounded yaw it relied on.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Idle-animation toggle on /pet

Adds an `idle: on/off` chip to `/pet`'s action-chip row, bound to the existing `animator.idle_animation.enabled` setting. Reads the current value from the state snapshot on mount, writes via `patchSetting` on click (optimistic, with revert on failure), and stays in sync with a `config.changed` subscription.

**Files:**
- Modify: `web/src/pet/pet.tsx`

- [ ] **Step 1: Add the `parseBool` helper**

In `web/src/pet/pet.tsx`, find the `ChatLine` type (currently line 10):

```ts
type ChatLine = { who: "you" | "lafufu"; text: string; emotion?: string; ts: number };
```

Add immediately after it:

```ts
/** Settings carry bools as strings ("true"/"1"); NATS config events may carry
 *  a real boolean. Normalize both. */
const parseBool = (v: unknown): boolean =>
  v === true || v === "true" || v === "1" || v === 1;
```

- [ ] **Step 2: Add the `idleOn` signal**

Find the signal declarations near the top of the `Pet` component (currently lines 19–27, ending with):

```ts
  const [discovered, setDiscovered] = createSignal<Set<string>>(new Set());
```

Add immediately after it:

```ts
  // Mirrors the animator.idle_animation.enabled setting. Defaults to true (the
  // setting's factory default); corrected on mount + by config.changed events.
  const [idleOn, setIdleOn] = createSignal(true);
```

- [ ] **Step 3: Add the `toggleIdle` handler**

Find the `requestMotion` function (currently lines 253–265). Add this new function immediately after `requestMotion`'s closing `};`:

```ts
  const toggleIdle = async () => {
    const next = !idleOn();
    setIdleOn(next); // optimistic
    try {
      await api.patchSetting("animator.idle_animation.enabled", {
        value: next,
        value_type: "bool",
      });
    } catch (e: any) {
      setIdleOn(!next); // revert
      toast.err("couldn't toggle idle animation", e.message);
    }
  };
```

- [ ] **Step 4: Read the setting on mount and subscribe to changes**

Find the `animator.pose` subscription inside `onMount` (as rewritten in Task 2, Step 6). Add this new subscription immediately after that `subs.push(...)` call:

```ts
    subs.push(nats.subscribe(
      "config.changed.animator.idle_animation.enabled",
      (f) => setIdleOn(parseBool(f.payload?.value)),
    ));
```

Then, find the `onCleanup(() => subs.forEach((u) => u()));` line (currently line 207). Add immediately after it:

```ts
    // Seed the idle toggle from the current server state.
    api.snapshot()
      .then((snap) => {
        const row = snap.settings.find(
          (s) => s.key === "animator.idle_animation.enabled",
        );
        if (row) setIdleOn(parseBool(row.value));
      })
      .catch(() => { /* keep the default-on state */ });
```

- [ ] **Step 5: Add the toggle chip to the action-chip row**

Find the "enable shake" button in the action-chip row (currently lines 504–506):

```ts
          <button class="btn btn--tiny" onClick={requestMotion}>
            enable shake
          </button>
```

Add this new button immediately after it (between "enable shake" and "hint"):

```ts
          <button
            class={`btn btn--tiny ${idleOn() ? "btn--primary" : ""}`}
            onClick={toggleIdle}
            title="Toggle the lafufu's idle 'living presence' animation. Off = the head holds where you drag it."
          >
            {idleOn() ? "idle: on" : "idle: off"}
          </button>
```

- [ ] **Step 6: Typecheck**

Run (from `web/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Run the unit tests**

Run (from `web/`): `npx vitest run`
Expected: PASS — all suites green (no new tests; this confirms nothing regressed).

- [ ] **Step 8: Manual verification (requires the running stack)**

From `web/`: `npm run dev`, open `/pet`:
- The action row shows an `idle: on` chip (highlighted).
- Tap it → becomes `idle: off`; drag the head, release → the head **holds** the dragged position.
- Tap it again → `idle: on`; drag the head, release → after ~1.5s the head **drifts back** to idle motion.
- Change `animator.idle_animation.enabled` from the admin Settings → "other" tab → the `/pet` chip updates to match without a reload.

If the stack is unavailable, Steps 6–7 are the completion gate.

- [ ] **Step 9: Commit**

```bash
git add web/src/pet/pet.tsx
git commit -m "$(cat <<'EOF'
feat(pet): on-page idle-animation toggle

Adds an idle: on/off chip to /pet bound to the existing
animator.idle_animation.enabled setting — read from the state snapshot
on mount, written via patchSetting (optimistic + revert), kept in sync
via a config.changed subscription. Off makes /pet a stable puppeteering
surface; on lets the head drift back to idle after a drag.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Drag aims head at absolute `head_lr`/`head_ud` targets within `SERVO_RANGES` | Task 1 (`applyDragDelta`), Task 2 (Steps 3–5) |
| Pixel→DXL helper, unit-tested | Task 1 |
| Throttled `animatorPreview` (~40ms) during drag | Task 2 (Step 2 `schedulePreview`/`flushPreview`) |
| Optimistic model update via `setPose` | Task 2 (Step 4) |
| Gesture decided by zone at pointer-down (puppeteer vs. ear-tug) | Task 2 (Step 3) |
| Feedback-loop fix: drop `head_lr`/`head_ud` from `animator.pose` during drag + 800ms grace | Task 2 (Steps 2 `headControlActive`, 6) |
| Remove `blend()` loop + `yaw`/`pitch` decay | Task 2 (Steps 2, 7, 8) |
| "Spin" easter egg reworked to sweep reversals | Task 2 (Steps 2 `trackSweep`, 5) |
| Pat / ear-tug / poke / shake unchanged | Task 2 (Steps 3, 5 — `handleTap` and `onMotion` untouched) |
| No 1s re-ping needed | By design — no re-ping code added |
| Idle toggle bound to `animator.idle_animation.enabled` | Task 3 |
| Read setting on mount via `api.snapshot()` | Task 3 (Step 4) |
| Write via `patchSetting`, optimistic + revert | Task 3 (Step 3) |
| Sync via `config.changed.…` subscription | Task 3 (Step 4) |
| Toggle chip in the bottom action-chip row | Task 3 (Step 5) |
| No backend / `pet_scene.ts` changes | Honored — only `head_drag.ts`, `head_drag.test.ts`, `pet.tsx` touched |

**Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to Task N" — every step shows complete code or an exact command. The drag-direction sign is intentionally caller-tunable with an explicit verification step (Task 2, Step 12), not a placeholder.

**Type consistency:** `HeadAxis` (`"head_lr"|"head_ud"`) is used consistently by `applyDragDelta`/`axisMid`. `gesture` (`"none"|"puppeteer"|"tug"`) is set in `onPointerDown` and read in `onPointerMove`/`onPointerUp`. `previewTimer`/`flushPreview`/`schedulePreview` names match across Steps 2, 5, 8. `idleOn`/`setIdleOn`/`parseBool`/`toggleIdle` names match across Task 3 steps. `api.snapshot()`, `api.patchSetting()`, `api.animatorPreview()` match their signatures in `web/src/shared/api.ts`. `api3d.setPose` accepts `Partial<Record<keyof SERVO_RANGES, number>>` per `pet_scene.ts`.
