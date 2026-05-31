import { describe, expect, it } from "vitest";
import { agentTopBadge } from "../src/shared/agent_state";

const NOW = 1_000_000;
const fresh = (overrides: Partial<Parameters<typeof agentTopBadge>[0]> = {}) =>
  agentTopBadge({
    wsConnected: true,
    lastSeenMs: NOW - 1_000, // 1s ago → live
    now: NOW,
    state: "idle",
    ...overrides,
  });

describe("agentTopBadge", () => {
  it("reports the bridge outage when the websocket is down", () => {
    const b = agentTopBadge({ wsConnected: false, lastSeenMs: NOW, now: NOW, state: "idle" });
    expect(b.label).toBe("disconnected");
    expect(b.pulse).toBe(false);
  });

  it("is offline once the agent heartbeat goes stale", () => {
    const b = fresh({ lastSeenMs: NOW - 90_000 }); // 90s ago, past the 60s window
    expect(b.label).toBe("offline");
    expect(b.color).toBe("var(--c-coral)");
  });

  it("staleness wins even if a state was last seen", () => {
    const b = fresh({ lastSeenMs: NOW - 90_000, state: "speaking" });
    expect(b.label).toBe("offline");
  });

  it("shows connecting before any agent signal has arrived", () => {
    const b = agentTopBadge({ wsConnected: true, lastSeenMs: null, now: NOW, state: null });
    expect(b.label).toBe("connecting…");
  });

  it("reads idle as plain 'online' at the top level", () => {
    const b = fresh({ state: "idle" });
    expect(b.label).toBe("online");
    expect(b.color).toBe("var(--c-moss)");
    expect(b.pulse).toBe(false);
  });

  it("surfaces 'warming up' while the agent warms", () => {
    const b = fresh({ state: "warming" });
    expect(b.label).toBe("warming up");
  });

  it("shows 'warming up' even before the first heartbeat (snapshot-seeded state)", () => {
    const b = agentTopBadge({ wsConnected: true, lastSeenMs: null, now: NOW, state: "warming" });
    expect(b.label).toBe("warming up");
  });

  it("defers active states to the shared stateBadge vocabulary", () => {
    expect(fresh({ state: "listening" }).label).toBe("listening");
    expect(fresh({ state: "thinking" }).label).toBe("thinking");
    expect(fresh({ state: "speaking" }).label).toBe("speaking");
    expect(fresh({ state: "wake_listening" }).label).toBe("waiting for wake word");
  });

  it("surfaces degraded as an unhealthy (coral) state", () => {
    const b = fresh({ state: "degraded" });
    expect(b.label).toBe("degraded");
    expect(b.color).toBe("var(--c-coral)");
  });
});
