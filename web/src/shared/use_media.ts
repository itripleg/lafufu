import { createSignal, onCleanup } from "solid-js";

/**
 * Reactive `window.matchMedia` wrapper.
 *
 * The admin UI styles almost everything with inline style objects, so plain CSS
 * media queries can't reach it. This returns a Solid accessor that tracks a
 * media query live (across resize / device rotation) so components can branch
 * their inline styles on the current viewport.
 *
 * Call inside a component's setup (or a `createRoot`) so `onCleanup` has an
 * owner to detach the listener. SSR/no-`matchMedia` environments get a static
 * `() => false`.
 */
export function createMediaQuery(query: string): () => boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => false;
  }
  const mql = window.matchMedia(query);
  const [matches, setMatches] = createSignal(mql.matches);
  const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
  mql.addEventListener("change", handler);
  onCleanup(() => mql.removeEventListener("change", handler));
  return matches;
}

export type LayoutMode = "mobile" | "long" | "desktop";

/** Phones — single hand, narrow. Everything stacks. */
export const MOBILE_QUERY = "(max-width: 640px)";
/** Any portrait viewport wider than a phone — a big monitor rotated on its
 *  side, or a tablet held upright. The floor is `MOBILE_QUERY` + 1px (not a
 *  bigger number) so there's no portrait dead-band that falls through to the
 *  side-by-side `desktop` layout and squashes it; phones stay in `mobile`
 *  because that query wins (see `useLayoutMode`). */
export const LONG_QUERY = "(orientation: portrait) and (min-width: 641px)";

/**
 * Collapse the two viewport queries into one layout mode. `mobile` wins over
 * `long` (a narrow portrait phone is mobile, not a tall-monitor layout);
 * everything else is `desktop` — the original, untouched layout.
 *
 * Like {@link createMediaQuery}, call this within a reactive owner (component
 * setup or `createRoot`) — it relies on `onCleanup` to detach both matchMedia
 * listeners, which silently no-ops without an owner.
 */
export function useLayoutMode(): () => LayoutMode {
  const isMobile = createMediaQuery(MOBILE_QUERY);
  const isLong = createMediaQuery(LONG_QUERY);
  return () => (isMobile() ? "mobile" : isLong() ? "long" : "desktop");
}
