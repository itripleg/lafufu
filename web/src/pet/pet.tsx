import { Component, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { applyDragDelta, axisMid, type DraggableAxis, type ServoRanges } from "./head_drag";
import { useServoConfig } from "../shared/use_servo_config";

/**
 * /pet — flat card pointed at the user. Drag tilts the card AND drives the
 * Lafufu's head servos. Keeps it simple: one image, CSS 3D transforms, no
 * three.js. Live `animator.pose` echoes adopt the new pose only on axes the
 * user isn't actively dragging, so the servo round-trip can't fight a drag.
 */

const MAX_YAW_DEG = 28;    // CSS rotateY at servo extremes
const MAX_PITCH_DEG = -22; // CSS rotateX — negative so drag-down tilts top toward viewer (face → floor)
const GRACE_MS = 800;     // how long after release we still ignore the echo

const Pet: Component = () => {
  const nats = new NatsWs();
  const config = useServoConfig(nats);

  // Signals start as undefined so we can gate on config being loaded.
  // Once config arrives, they're seeded from axisMid (or the live pose).
  const [headLr, setHeadLr] = createSignal<number | undefined>(undefined);
  const [headUd, setHeadUd] = createSignal<number | undefined>(undefined);
  const [dragging, setDragging] = createSignal(false);

  let lastPose: Record<string, number> = {};
  const axisHoldTs: Record<DraggableAxis, number> = {
    head_lr: 0, head_ud: 0, eye: 0, jaw: 0,
  };
  const axisOwned = (a: DraggableAxis) =>
    performance.now() - axisHoldTs[a] < GRACE_MS;

  // Seed the head signals once config is available (and not already set by
  // a live pose that arrived first).
  createEffect(() => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!ranges) return;
    if (headLr() === undefined) setHeadLr(lastPose.head_lr ?? axisMid("head_lr", ranges));
    if (headUd() === undefined) setHeadUd(lastPose.head_ud ?? axisMid("head_ud", ranges));
  });

  // Helper: convert a DXL value to a signed [-1, 1] fraction using live ranges.
  const signedFor = (dxl: number, axis: DraggableAxis): number => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!ranges) return 0;
    const [lo, hi] = ranges[axis];
    const n = Math.max(0, Math.min(1, (dxl - lo) / (hi - lo)));
    return n * 2 - 1;
  };

  // Throttled servo command — coalesces axis updates, fires at most every
  // ~40ms. A throttle so the servos keep tracking during a continuous drag.
  let previewTimer: ReturnType<typeof setTimeout> | undefined;
  const pending: Partial<Record<DraggableAxis, number>> = {};
  const flushPreview = () => {
    previewTimer = undefined;
    for (const k of Object.keys(pending) as DraggableAxis[]) {
      const v = pending[k];
      if (v !== undefined) {
        api.animatorPreview(k, Math.round(v)).catch(() => {});
        delete pending[k];
      }
    }
  };
  const queuePreview = (axis: DraggableAxis, value: number) => {
    pending[axis] = value;
    if (previewTimer === undefined) previewTimer = setTimeout(flushPreview, 40);
  };

  // CSS transform — drag-down should tilt the card's TOP toward the viewer
  // (face angles up toward the camera's chin level), matching the Lafufu when
  // its head_ud servo runs to the high end. Adjust sign once and the whole
  // pipeline stays consistent.
  const transform = createMemo(() => {
    const lr = headLr();
    const ud = headUd();
    if (lr === undefined || ud === undefined) return "none";
    const yawDeg = signedFor(lr, "head_lr") * MAX_YAW_DEG;
    const pitchDeg = signedFor(ud, "head_ud") * MAX_PITCH_DEG;
    return `perspective(900px) rotateX(${pitchDeg}deg) rotateY(${yawDeg}deg)`;
  });

  let lastX = 0, lastY = 0;
  const onPointerDown = (e: PointerEvent) => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!ranges) return; // don't start drag before config is loaded
    setDragging(true);
    lastX = e.clientX;
    lastY = e.clientY;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    // Grab the lafufu wherever it currently is (so the drag picks up from
    // the live pose instead of jumping back to the midpoint).
    setHeadLr(lastPose.head_lr ?? axisMid("head_lr", ranges));
    setHeadUd(lastPose.head_ud ?? axisMid("head_ud", ranges));
  };
  const onPointerMove = (e: PointerEvent) => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!dragging() || !ranges) return;
    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    const currentLr = headLr() ?? axisMid("head_lr", ranges);
    const currentUd = headUd() ?? axisMid("head_ud", ranges);
    const newLr = applyDragDelta("head_lr", currentLr, dx, ranges);
    const newUd = applyDragDelta("head_ud", currentUd, dy, ranges);
    setHeadLr(newLr);
    setHeadUd(newUd);
    const now = performance.now();
    axisHoldTs.head_lr = now;
    axisHoldTs.head_ud = now;
    queuePreview("head_lr", newLr);
    queuePreview("head_ud", newUd);
  };
  const onPointerUp = () => {
    if (!dragging()) return;
    setDragging(false);
    // Hold the echo-suppression grace window past release so the servo
    // round-trip can't snap the card back before the lafufu settles.
    const now = performance.now();
    axisHoldTs.head_lr = now;
    axisHoldTs.head_ud = now;
    if (previewTimer !== undefined) {
      clearTimeout(previewTimer);
      previewTimer = undefined;
    }
    flushPreview();
  };

  onMount(() => {
    nats.start();
    const unsub = nats.subscribe("animator.pose", (f) => {
      lastPose = f.payload;
      // Adopt only the axes the user isn't owning right now.
      if (!axisOwned("head_lr") && typeof f.payload.head_lr === "number") {
        setHeadLr(f.payload.head_lr);
      }
      if (!axisOwned("head_ud") && typeof f.payload.head_ud === "number") {
        setHeadUd(f.payload.head_ud);
      }
    });
    onCleanup(unsub);
  });

  onCleanup(() => {
    if (previewTimer !== undefined) clearTimeout(previewTimer);
    nats.stop();
  });

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background:
          "radial-gradient(circle at 50% 30%, #2d2018 0%, #1a1410 60%, #0c0907 100%)",
        display: "flex",
        "align-items": "center",
        "justify-content": "center",
        overflow: "hidden",
        "touch-action": "none",
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <img
        src="/lafufu.png"
        alt="lafufu"
        draggable={false}
        style={{
          width: "min(70vmin, 480px)",
          height: "auto",
          "filter": "drop-shadow(0 24px 40px rgba(0,0,0,.55))",
          transform: transform(),
          "transform-style": "preserve-3d",
          transition: dragging() ? "none" : "transform 0.3s ease-out",
          "user-select": "none",
          "pointer-events": "none",
        }}
      />
    </div>
  );
};

export default Pet;
