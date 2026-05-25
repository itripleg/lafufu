import { describe, expect, it } from "vitest";
import { mergeHistory, stateBadge, type Entry } from "../src/admin/chat_log";

const e = (role: Entry["role"], text: string, ts = 0): Entry => ({ role, text, ts });

describe("mergeHistory", () => {
  it("returns history when no live entries arrived during the fetch", () => {
    const history = [e("user", "hi"), e("lafufu", "hello")];
    expect(mergeHistory(history, [])).toEqual(history);
  });

  it("returns the live entries when history is empty", () => {
    const live = [e("user", "hi")];
    expect(mergeHistory([], live)).toEqual(live);
  });

  it("places history before entries that arrived live during the fetch", () => {
    const history = [e("user", "old q"), e("lafufu", "old a")];
    const live = [e("user", "new q"), e("lafufu", "new a")];
    expect(mergeHistory(history, live)).toEqual([...history, ...live]);
  });

  it("drops a live entry that duplicates the tail of history", () => {
    const history = [e("user", "q"), e("lafufu", "shared reply")];
    const live = [e("lafufu", "shared reply"), e("user", "next")];
    expect(mergeHistory(history, live)).toEqual([history[0], history[1], live[1]]);
  });

  it("keeps a live entry that matches text but not role", () => {
    const history = [e("user", "echo")];
    const live = [e("lafufu", "echo")];
    expect(mergeHistory(history, live)).toEqual([...history, ...live]);
  });

  it("caps the merged result at the most recent 100 entries", () => {
    const history = Array.from({ length: 90 }, (_, i) => e("user", `h${i}`));
    const live = Array.from({ length: 30 }, (_, i) => e("lafufu", `l${i}`));
    const merged = mergeHistory(history, live);
    expect(merged.length).toBe(100);
    expect(merged[merged.length - 1]).toEqual(live[29]);
  });
});

describe("stateBadge", () => {
  it("distinguishes wake_listening from listening", () => {
    expect(stateBadge("wake_listening").label).toBe("waiting for wake word");
    expect(stateBadge("listening").label).toBe("listening");
    // Different labels so the operator can tell which slot we're in.
    expect(stateBadge("wake_listening").label).not.toBe(stateBadge("listening").label);
  });

  it("returns a connecting placeholder before the first state event", () => {
    expect(stateBadge(null).label).toBe("connecting…");
    expect(stateBadge(undefined).label).toBe("connecting…");
    expect(stateBadge("").label).toBe("connecting…");
  });

  it("pulses for active states only", () => {
    expect(stateBadge("wake_listening").pulse).toBe(true);
    expect(stateBadge("listening").pulse).toBe(true);
    expect(stateBadge("transcribing").pulse).toBe(true);
    expect(stateBadge("thinking").pulse).toBe(true);
    expect(stateBadge("speaking").pulse).toBe(true);
    expect(stateBadge("warming").pulse).toBe(true);
    expect(stateBadge("idle").pulse).toBe(false);
    expect(stateBadge("degraded").pulse).toBe(false);
    expect(stateBadge("shutdown").pulse).toBe(false);
    expect(stateBadge(null).pulse).toBe(false);
  });

  it("covers every state in the AgentStateName literal", () => {
    // Mirror of packages/shared/src/lafufu_shared/schemas.py AgentStateName.
    // Any new state added there should also surface a meaningful badge.
    const states = [
      "warming",
      "idle",
      "wake_listening",
      "listening",
      "transcribing",
      "thinking",
      "speaking",
      "degraded",
      "shutdown",
    ];
    for (const s of states) {
      const b = stateBadge(s);
      expect(b.label).not.toBe("connecting…");
      expect(b.color).toMatch(/^var\(--c-/);
    }
  });
});
