/**
 * Human-readable label + visual hints for each agent.state.*.
 *
 * Single source of truth so the admin chat widget badge and the public face
 * kiosk show the same friendly labels (and so `wake_listening` doesn't leak
 * to venue visitors as a raw token).
 */
export interface StateBadge {
  label: string;
  color: string;
  /** True if the badge should pulse — visually marks "actively doing work". */
  pulse: boolean;
}

export const stateBadge = (name: string | null | undefined): StateBadge => {
  switch (name) {
    case "warming":         return { label: "warming up",            color: "var(--c-amber)", pulse: true };
    case "wake_listening":  return { label: "waiting for wake word", color: "var(--c-mist)",  pulse: true };
    case "listening":       return { label: "listening",             color: "var(--c-moss)",  pulse: true };
    case "transcribing":    return { label: "transcribing",          color: "var(--c-amber)", pulse: true };
    case "thinking":        return { label: "thinking",              color: "var(--c-amber)", pulse: true };
    case "speaking":        return { label: "speaking",              color: "var(--c-moss)",  pulse: true };
    case "degraded":        return { label: "degraded",              color: "var(--c-coral)", pulse: false };
    case "shutdown":        return { label: "shutting down",         color: "var(--c-stone)", pulse: false };
    case "idle":            return { label: "idle",                  color: "var(--c-stone)", pulse: false };
    default:                return { label: "connecting…",           color: "var(--c-stone)", pulse: false };
  }
};

/** How long without an agent heartbeat before we call the agent offline.
 *  Matches service_status.tsx's window: heartbeats land every 5 s. */
const AGENT_OFFLINE_AFTER_MS = 60_000;

/**
 * Top-of-page "is lafufu up, and what's it doing?" badge.
 *
 * Unlike the old header pill (green the instant ANY service heartbeats), this
 * reflects the AGENT's own liveness + state:
 *   - websocket down       → "disconnected" (we can't observe the agent at all)
 *   - agent heartbeat stale → "offline"      (bridge up, agent process gone)
 *   - nothing seen yet      → "connecting…"
 *   - idle                  → "online"        (ready/healthy, reads better than "idle")
 *   - everything else       → the shared stateBadge ("warming up", "listening", …)
 *
 * `lastSeenMs` is the browser-clock time of the last system.heartbeat.agent
 * (null = never seen). `state` is the latest agent.state.* tail.
 */
export const agentTopBadge = (opts: {
  wsConnected: boolean;
  lastSeenMs: number | null;
  now: number;
  state: string | null | undefined;
  offlineAfterMs?: number;
}): StateBadge => {
  const offlineAfter = opts.offlineAfterMs ?? AGENT_OFFLINE_AFTER_MS;
  // The browser's NATS bridge (control) is unreachable — report the outage
  // rather than a stale agent state we can no longer trust.
  if (!opts.wsConnected) return { label: "disconnected", color: "var(--c-stone)", pulse: false };
  // We saw the agent heartbeat, but it has since gone quiet → agent is down.
  if (opts.lastSeenMs !== null && opts.now - opts.lastSeenMs >= offlineAfter)
    return { label: "offline", color: "var(--c-coral)", pulse: false };
  // Bridge up but the agent has never reported in (no heartbeat, no state yet).
  if (opts.lastSeenMs === null && !opts.state)
    return { label: "connecting…", color: "var(--c-stone)", pulse: false };
  // Agent is reporting in. "idle" is the plain healthy/online state at the top
  // level; everything else defers to the shared stateBadge vocabulary.
  if (!opts.state || opts.state === "idle")
    return { label: "online", color: "var(--c-moss)", pulse: false };
  return stateBadge(opts.state);
};
