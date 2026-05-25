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
