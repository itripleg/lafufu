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
