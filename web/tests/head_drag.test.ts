import { describe, expect, it } from "vitest";
import { applyDragDelta, axisMid, clamp, type ServoRanges } from "../src/pet/head_drag";

const RANGES: ServoRanges = {
  head_lr: [1828, 2298],
  head_ud: [2885, 3278],
  eye:     [1995, 2085],
  jaw:     [1594, 1811],
  brow:    [2056, 2087],
};

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
    expect(axisMid("head_lr", RANGES)).toBe((RANGES.head_lr[0] + RANGES.head_lr[1]) / 2);
    expect(axisMid("head_ud", RANGES)).toBe((RANGES.head_ud[0] + RANGES.head_ud[1]) / 2);
  });
});

describe("applyDragDelta", () => {
  it("a positive delta increases the value", () => {
    const mid = axisMid("head_lr", RANGES);
    expect(applyDragDelta("head_lr", mid, 50, RANGES)).toBeGreaterThan(mid);
  });
  it("a negative delta decreases the value", () => {
    const mid = axisMid("head_ud", RANGES);
    expect(applyDragDelta("head_ud", mid, -50, RANGES)).toBeLessThan(mid);
  });
  it("clamps at the high end of head_lr", () => {
    expect(applyDragDelta("head_lr", RANGES.head_lr[1], 10000, RANGES)).toBe(RANGES.head_lr[1]);
  });
  it("clamps at the low end of head_ud", () => {
    expect(applyDragDelta("head_ud", RANGES.head_ud[0], -10000, RANGES)).toBe(RANGES.head_ud[0]);
  });
  it("clamps at the low end of head_lr", () => {
    expect(applyDragDelta("head_lr", RANGES.head_lr[0], -10000, RANGES)).toBe(RANGES.head_lr[0]);
  });
  it("clamps at the high end of head_ud", () => {
    expect(applyDragDelta("head_ud", RANGES.head_ud[1], 10000, RANGES)).toBe(RANGES.head_ud[1]);
  });
  it("never returns a value outside the axis range", () => {
    const [lo, hi] = RANGES.head_lr;
    for (const delta of [-9999, -100, 0, 100, 9999]) {
      const out = applyDragDelta("head_lr", axisMid("head_lr", RANGES), delta, RANGES);
      expect(out).toBeGreaterThanOrEqual(lo);
      expect(out).toBeLessThanOrEqual(hi);
    }
  });
});
