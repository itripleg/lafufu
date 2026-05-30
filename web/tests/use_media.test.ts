import { afterEach, describe, expect, it, vi } from "vitest";
import { createRoot } from "solid-js";
import {
  LONG_QUERY,
  MOBILE_QUERY,
  createMediaQuery,
  useLayoutMode,
} from "../src/shared/use_media";

// jsdom has no matchMedia — install a controllable fake. Each query gets its
// own MediaQueryList whose `matches` we can flip and whose "change" listeners
// we can fire, so we can simulate a resize / rotation.
interface FakeMql {
  matches: boolean;
  listeners: Set<(e: { matches: boolean }) => void>;
}
function installMatchMedia(initial: Record<string, boolean>) {
  const store = new Map<string, FakeMql>();
  // Every event type the hook subscribes to, so a test can assert it listens
  // on "change" specifically (and would fail if that ever regressed).
  const eventTypes = new Set<string>();
  const get = (q: string): FakeMql => {
    let m = store.get(q);
    if (!m) {
      m = { matches: initial[q] ?? false, listeners: new Set() };
      store.set(q, m);
    }
    return m;
  };
  window.matchMedia = ((query: string) => {
    const m = get(query);
    return {
      get matches() { return m.matches; },
      media: query,
      addEventListener: (type: string, cb: (e: { matches: boolean }) => void) => {
        eventTypes.add(type);
        m.listeners.add(cb);
      },
      removeEventListener: (_: string, cb: (e: { matches: boolean }) => void) => m.listeners.delete(cb),
      // legacy API, unused by the hook but part of the type
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => true,
    } as unknown as MediaQueryList;
  }) as typeof window.matchMedia;

  return {
    set(query: string, matches: boolean) {
      const m = get(query);
      m.matches = matches;
      for (const cb of m.listeners) cb({ matches });
    },
    listenerCount: (query: string) => get(query).listeners.size,
    eventTypes,
  };
}

afterEach(() => {
  // @ts-expect-error — reset between tests
  delete window.matchMedia;
  vi.restoreAllMocks();
});

describe("createMediaQuery", () => {
  it("tracks the initial match and live changes", () => {
    const mm = installMatchMedia({ "(max-width: 640px)": false });
    createRoot((dispose) => {
      const isMobile = createMediaQuery("(max-width: 640px)");
      expect(isMobile()).toBe(false);
      mm.set("(max-width: 640px)", true);
      expect(isMobile()).toBe(true);
      dispose();
    });
  });

  it("subscribes to the 'change' event and detaches on cleanup", () => {
    const mm = installMatchMedia({ "(max-width: 640px)": false });
    createRoot((dispose) => {
      createMediaQuery("(max-width: 640px)");
      expect(mm.listenerCount("(max-width: 640px)")).toBe(1);
      // Must listen on "change" — not "resize" or anything else.
      expect([...mm.eventTypes]).toEqual(["change"]);
      dispose();
    });
    expect(mm.listenerCount("(max-width: 640px)")).toBe(0);
  });

  it("falls back to false when matchMedia is unavailable", () => {
    // no installMatchMedia → window.matchMedia is undefined
    createRoot((dispose) => {
      const m = createMediaQuery("(max-width: 640px)");
      expect(m()).toBe(false);
      dispose();
    });
  });
});

describe("useLayoutMode", () => {
  it("returns desktop when neither query matches", () => {
    installMatchMedia({ [MOBILE_QUERY]: false, [LONG_QUERY]: false });
    createRoot((dispose) => {
      expect(useLayoutMode()()).toBe("desktop");
      dispose();
    });
  });

  it("returns long for a wide portrait viewport", () => {
    installMatchMedia({ [MOBILE_QUERY]: false, [LONG_QUERY]: true });
    createRoot((dispose) => {
      expect(useLayoutMode()()).toBe("long");
      dispose();
    });
  });

  it("mobile wins even when the long query also matches", () => {
    // a narrow portrait phone could match both — mobile must take priority
    installMatchMedia({ [MOBILE_QUERY]: true, [LONG_QUERY]: true });
    createRoot((dispose) => {
      expect(useLayoutMode()()).toBe("mobile");
      dispose();
    });
  });

  it("reacts to a rotation from desktop to long", () => {
    const mm = installMatchMedia({ [MOBILE_QUERY]: false, [LONG_QUERY]: false });
    createRoot((dispose) => {
      const mode = useLayoutMode();
      expect(mode()).toBe("desktop");
      mm.set(LONG_QUERY, true);
      expect(mode()).toBe("long");
      dispose();
    });
  });

  it("reacts to a viewport shrink into mobile", () => {
    const mm = installMatchMedia({ [MOBILE_QUERY]: false, [LONG_QUERY]: false });
    createRoot((dispose) => {
      const mode = useLayoutMode();
      expect(mode()).toBe("desktop");
      mm.set(MOBILE_QUERY, true);
      expect(mode()).toBe("mobile");
      dispose();
    });
  });

  it("reverts from long back to desktop when the portrait query clears", () => {
    const mm = installMatchMedia({ [MOBILE_QUERY]: false, [LONG_QUERY]: true });
    createRoot((dispose) => {
      const mode = useLayoutMode();
      expect(mode()).toBe("long");
      mm.set(LONG_QUERY, false);
      expect(mode()).toBe("desktop");
      dispose();
    });
  });
});
