import { describe, expect, it } from "vitest";
import { applyDragDelta, axisMid, clamp } from "../src/pet/head_drag";
import { SERVO_RANGES } from "../src/pet/servo_ranges";

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
  it("clamps at the low end of head_lr", () => {
    expect(applyDragDelta("head_lr", SERVO_RANGES.head_lr[0], -10000)).toBe(
      SERVO_RANGES.head_lr[0],
    );
  });
  it("clamps at the high end of head_ud", () => {
    expect(applyDragDelta("head_ud", SERVO_RANGES.head_ud[1], 10000)).toBe(
      SERVO_RANGES.head_ud[1],
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
