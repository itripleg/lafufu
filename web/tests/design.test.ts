import { describe, expect, it } from "vitest";
import { emotionToColor, rmsToHeightPct } from "../src/shared/design";

describe("emotionToColor", () => {
  it("returns specific color for known emotions", () => {
    expect(emotionToColor("happy")).toBe("#fcd34d");
    expect(emotionToColor("sad")).toBe("#60a5fa");
  });
  it("falls back to neutral for unknown/missing", () => {
    expect(emotionToColor(undefined)).toBe("#94a3b8");
    expect(emotionToColor("madeupemotion")).toBe("#94a3b8");
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
