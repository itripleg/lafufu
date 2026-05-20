import { createSignal } from "solid-js";

/**
 * Client-side auth state. The control API uses an optional shared token
 * (see packages/control/.../auth.py): loopback/kiosk and no-token-configured
 * deployments never see the lock screen — `locked` only flips true when a
 * request actually comes back 401.
 */
const [locked, setLocked] = createSignal(false);

export { locked };

/** Called by the API layer on any 401 — raises the lock screen. */
export function reportUnauthorized(): void {
  setLocked(true);
}
