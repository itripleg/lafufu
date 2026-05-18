import { describe, expect, it } from "vitest";
import { emotionToColor, rmsToHeightPct } from "../src/shared/design";

describe("emotionToColor", () => {
  it("returns specific color for known emotions", () => {
    expect(emotionToColor("happy")).toBe("#e4b15a");
    expect(emotionToColor("sad")).toBe("#8a9bc4");
  });
  it("falls back to neutral for unknown/missing", () => {
    expect(emotionToColor(undefined)).toBe("#b9ad94");
    expect(emotionToColor("madeupemotion")).toBe("#b9ad94");
  });
});

describe("rmsToHeightPct", () => {
  it("clamps to 0..100", () => {
    expect(rmsToHeightPct(0)).toBe(0);
    expect(rmsToHeightPct(1)).toBe(100);
    expect(rmsToHeightPct(-0.5)).toBe(0);
    expect(rmsToHeightPct(2)).toBe(100);
  });
});
