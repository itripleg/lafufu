import { describe, expect, it } from "vitest";
import { locked, reportUnauthorized } from "../src/shared/auth";

describe("auth lock state", () => {
  it("starts unlocked", () => {
    expect(locked()).toBe(false);
  });

  it("reportUnauthorized raises the lock screen", () => {
    reportUnauthorized();
    expect(locked()).toBe(true);
  });
});
