import { describe, expect, it, vi } from "vitest";
import { createRoot } from "solid-js";
import { createReactiveResource } from "../src/shared/reactive_resource";

// Minimal stub for the NatsWs `subscribe` contract.
function makeStubNats() {
  const subs = new Map<string, Set<() => void>>();
  const subscribe = (topic: string, handler: () => void) => {
    const set = subs.get(topic) ?? new Set();
    set.add(handler);
    subs.set(topic, set);
    return () => set.delete(handler);
  };
  const emit = (topic: string) => {
    for (const h of subs.get(topic) ?? []) h();
  };
  return { subs, subscribe, emit };
}

describe("createReactiveResource", () => {
  it("fetches once on mount and refetches when a subscribed topic fires", async () => {
    const stub = makeStubNats();
    let calls = 0;
    const fetchFn = vi.fn(async () => {
      calls += 1;
      return { value: calls };
    });

    await createRoot(async (dispose) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const [data] = createReactiveResource(fetchFn, ["foo.changed"], stub as any);
      await new Promise((r) => setTimeout(r, 0));
      expect(data()).toEqual({ value: 1 });
      stub.emit("foo.changed");
      await new Promise((r) => setTimeout(r, 0));
      expect(data()).toEqual({ value: 2 });
      dispose();
    });
  });

  it("exposes refetch so callers can drive immediate post-mutation reload", async () => {
    const stub = makeStubNats();
    let calls = 0;
    const fetchFn = vi.fn(async () => {
      calls += 1;
      return calls;
    });
    await createRoot(async (dispose) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const [data, refetch] = createReactiveResource(fetchFn, ["x.changed"], stub as any);
      await new Promise((r) => setTimeout(r, 0));
      expect(data()).toBe(1);
      await refetch();
      expect(data()).toBe(2);
      dispose();
    });
  });

  it("unsubscribes on cleanup", async () => {
    const stub = makeStubNats();
    const fetchFn = vi.fn(async () => 0);
    await createRoot(async (dispose) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      createReactiveResource(fetchFn, ["bar.changed"], stub as any);
      expect(stub.subs.get("bar.changed")?.size).toBe(1);
      dispose();
    });
    expect(stub.subs.get("bar.changed")?.size ?? 0).toBe(0);
  });

  // NB: a "swallows refetch errors" test was intentionally not added here.
  // Solid's dev-mode createResource treats a rejected refetch promise as an
  // unhandled error in the test scope unless the test reads `data.error`,
  // which would require breaking the `[data, refetch]` return contract.
  // The production behavior — console.warn on a rejected NATS-driven
  // refetch, data accessor preserving its last good value — is exercised
  // and visible to operators in devtools. See reactive_resource.ts for
  // the catch logic.
});
