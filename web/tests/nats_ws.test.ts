import { describe, expect, it } from "vitest";
import { matchesPattern } from "../src/shared/nats_ws";

describe("matchesPattern", () => {
  it("matches exact topic", () => {
    expect(matchesPattern("agent.state.idle", "agent.state.idle")).toBe(true);
    expect(matchesPattern("agent.state.idle", "agent.state.thinking")).toBe(false);
  });
  it("supports * for one token", () => {
    expect(matchesPattern("agent.state.*", "agent.state.idle")).toBe(true);
    expect(matchesPattern("agent.state.*", "agent.state.x.y")).toBe(false);
  });
  it("supports > for tail", () => {
    expect(matchesPattern("agent.>", "agent.state.idle")).toBe(true);
    expect(matchesPattern("agent.>", "agent.transcript")).toBe(true);
    expect(matchesPattern(">", "anything.you.want")).toBe(true);
  });
  it("rejects short topic", () => {
    expect(matchesPattern("a.b.c", "a.b")).toBe(false);
  });
});
