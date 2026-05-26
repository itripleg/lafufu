/**
 * Pure helpers for drag → head-servo mapping on the /pet page.
 *
 * Keeps the pixel-delta → DXL-position math out of the SolidJS component so it
 * can be unit-tested without three.js or the DOM. `SERVO_RANGES` is the single
 * source of truth for the servo bounds — imported, not duplicated.
 */
import { SERVO_RANGES } from "./pet_scene";

/** Servos the /pet page lets the user drag directly. */
export type DraggableAxis = "head_lr" | "head_ud" | "eye" | "jaw";

/** Back-compat alias — earlier code & tests only knew about the two head axes. */
export type HeadAxis = "head_lr" | "head_ud";

/** Pixels of drag that span one servo's full range. ~500px is a comfortable
 *  phone-screen swipe; uniform across axes for predictable feel. */
const PX_PER_FULL_RANGE = 500;

export const LR_PER_PX =
  (SERVO_RANGES.head_lr[1] - SERVO_RANGES.head_lr[0]) / PX_PER_FULL_RANGE;
export const UD_PER_PX =
  (SERVO_RANGES.head_ud[1] - SERVO_RANGES.head_ud[0]) / PX_PER_FULL_RANGE;

/** Clamp `v` into the inclusive `[lo, hi]` range. */
export const clamp = (v: number, lo: number, hi: number): number =>
  Math.max(lo, Math.min(hi, v));

/** Midpoint of an axis range — the seed used before any live pose is known. */
export const axisMid = (axis: DraggableAxis): number => {
  const [lo, hi] = SERVO_RANGES[axis];
  return (lo + hi) / 2;
};

/**
 * Apply a pixel drag delta to a current DXL value, clamped to the axis range.
 * Sign is caller-controlled: pass `dx`/`dy` (or their negation) so the servo
 * moves the intended way for the rig.
 */
export const applyDragDelta = (
  axis: DraggableAxis,
  currentDxl: number,
  deltaPx: number,
): number => {
  const [lo, hi] = SERVO_RANGES[axis];
  const perPx = (hi - lo) / PX_PER_FULL_RANGE;
  return clamp(currentDxl + deltaPx * perPx, lo, hi);
};
