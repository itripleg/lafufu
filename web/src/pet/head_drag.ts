/**
 * Pure helpers for drag → head-servo mapping on the /pet page.
 *
 * Keeps the pixel-delta → DXL-position math out of the SolidJS component so it
 * can be unit-tested without three.js or the DOM. Ranges are passed as a
 * parameter (fetched from GET /api/animator/config via useServoConfig) so the
 * module has no static dependency on servo bounds.
 */

/** Full set of servo ranges, keyed by servo name. */
export type ServoRanges = Record<
  "head_lr" | "head_ud" | "eye" | "jaw" | "brow",
  readonly [number, number]
>;

/** Servos the /pet page lets the user drag directly. */
export type DraggableAxis = "head_lr" | "head_ud" | "eye" | "jaw";

/** Pixels of drag that span one servo's full range. ~500px is a comfortable
 *  phone-screen swipe; uniform across axes for predictable feel. */
const PX_PER_FULL_RANGE = 500;

/** Clamp `v` into the inclusive `[lo, hi]` range. */
export const clamp = (v: number, lo: number, hi: number): number =>
  Math.max(lo, Math.min(hi, v));

/** Midpoint of an axis range — the seed used before any live pose is known. */
export const axisMid = (axis: DraggableAxis, ranges: ServoRanges): number => {
  const [lo, hi] = ranges[axis];
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
  ranges: ServoRanges,
): number => {
  const [lo, hi] = ranges[axis];
  const perPx = (hi - lo) / PX_PER_FULL_RANGE;
  return clamp(currentDxl + deltaPx * perPx, lo, hi);
};
