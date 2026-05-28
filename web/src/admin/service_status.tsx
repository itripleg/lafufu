/**
 * Service status pills — one row per known service.
 *
 * Single source of truth: `lastSeenMs` per service, set on the browser's clock
 * the moment a frame from that service is received via the NATS WS bridge.
 * No server-time math, no hysteresis, no lifecycle echoes shown.
 *
 * Liveness rules:
 *   - We received a frame from svc within OFFLINE_AFTER_MS → online.
 *   - We've never received one → no signal.
 *   - Otherwise → offline.
 *
 * Every received frame also refreshes control's row, because every frame had
 * to pass through control's WS bridge — that's its proof of life. Without
 * this, control (which publishes nothing except its own 5 s heartbeat) would
 * be the only service that could "go offline" purely from event-loop jitter.
 */
import { Component, createSignal, onCleanup, onMount, For, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

interface Row {
  name: string;
  /** Browser Date.now() at the most recent frame from this service. null = never seen.
   *  Ignored for the "control" row — that row's liveness comes from the WS connection
   *  state itself, since every frame had to pass through control to reach us. */
  lastSeenMs: number | null;
  /** Latest *.state.* value, if any was published. Not all services emit state. */
  state?: string;
  /** Animator-only: false when running without a U2D2 / no servos attached. */
  hasU2D2?: boolean;
}

const KNOWN_SERVICES = ["agent", "animator", "printer", "control"];

/** A service must have been silent for this long before it's flagged offline.
 *  Services heartbeat every 5 s plus emit state / events between, so anything
 *  short of ~12 missed heartbeats is normal under-load jitter, not downtime. */
const OFFLINE_AFTER_MS = 60_000;

/** Per-service min ms between row updates. Stops 20 Hz topics (animator.pose,
 *  agent.tts.rms) from thrashing setRows. Updates are batched into one bump
 *  per service per THROTTLE_MS so the row simply stays "online". */
const THROTTLE_MS = 1500;

/** UI tick rate — drives age recomputation in render. 1 s is fine since
 *  the offline threshold is 60 s. */
const TICK_MS = 1000;

/** Active-work states get an amber dot so the operator can see at a glance
 *  that the service is doing something rather than idling. */
const BUSY_STATES = new Set(["speaking", "listening", "transcribing", "thinking"]);

/** States that mean the service is up but unhealthy. */
const ERROR_STATES = new Set(["degraded", "error"]);

export const ServiceStatus: Component<{ nats: NatsWs }> = (props) => {
  const initialRows = (): Record<string, Row> =>
    Object.fromEntries(KNOWN_SERVICES.map((n) => [n, { name: n, lastSeenMs: null }]));
  const [rows, setRows] = createSignal<Record<string, Row>>(initialRows());
  const [tick, setTick] = createSignal(Date.now());
  const [restarting, setRestarting] = createSignal<string | null>(null);
  // Liveness of control is the WS connection itself — if the browser is
  // connected, control is alive by definition.
  const [wsConnected, setWsConnected] = createSignal(props.nats.isConnected());

  let interval: number | undefined;
  let unsubAll: (() => void) | undefined;
  let unsubState: (() => void) | undefined;
  let unsubConn: (() => void) | undefined;

  // Per-service throttle clocks. Kept outside the rows so the high-frequency
  // path is a Map lookup, not a Solid signal read.
  const lastUpdate = new Map<string, number>();

  /** Map a NATS topic to the service that owns it, or null. */
  const ownerOfTopic = (topic: string): string | null => {
    const parts = topic.split(".");
    const head = parts[0];
    if (head === "system") {
      // system.heartbeat.<svc> / system.service.<event> (payload has .service) /
      // system.error.<svc>.<kind>
      // For system.heartbeat.<svc> + system.error.<svc>.* we read the 3rd token.
      // For system.service.<event> the owner is in the payload, not the topic —
      // those are rare and the catch-all picks them up via that payload below.
      return parts.length >= 3 && KNOWN_SERVICES.includes(parts[2]) ? parts[2] : null;
    }
    return KNOWN_SERVICES.includes(head) ? head : null;
  };

  /** Try to update svc's row; returns true if the throttle allowed it. */
  const bump = (svc: string, now: number): boolean => {
    const prev = lastUpdate.get(svc) ?? 0;
    if (now - prev < THROTTLE_MS) return false;
    lastUpdate.set(svc, now);
    return true;
  };

  onMount(() => {
    // Control's liveness is the WS connection, not a frame counter. The browser
    // is talking to control directly via /ws — if that connection is open,
    // control is alive. Saves us from inventing a heartbeat-jitter story for
    // the one service that doesn't publish anything besides heartbeats.
    unsubConn = props.nats.onConnection((connected) => setWsConnected(connected));

    // Single catch-all subscription handles liveness for every OTHER service.
    // We do NOT update control's row from frames — control's liveness is the
    // WS connection state (above), which is more accurate than counting frames.
    unsubAll = props.nats.subscribe(">", (f) => {
      const now = Date.now();
      const owner = ownerOfTopic(f.topic);
      // system.service.<event> doesn't encode owner in topic — pull it from
      // payload so lifecycle events still count as liveness.
      const lifecycleOwner =
        f.topic.startsWith("system.service.") && typeof (f.payload as { service?: string })?.service === "string"
          ? (f.payload as { service: string }).service
          : null;
      const o = owner ?? (lifecycleOwner && KNOWN_SERVICES.includes(lifecycleOwner) ? lifecycleOwner : null);
      if (!o || o === "control") return;
      if (!bump(o, now)) return;

      setRows((r) => ({
        ...r,
        [o]: {
          ...(r[o] ?? { name: o, lastSeenMs: null }),
          lastSeenMs: now,
        },
      }));
    });

    // Capture state + has_u2d2 from each service's state events. The catch-all
    // above already bumped lastSeenMs; this handler only writes the labels.
    unsubState = props.nats.subscribe("*.state.*", (f) => {
      const parts = f.topic.split(".");
      if (parts[1] !== "state") return;
      const name = parts[0];
      if (!KNOWN_SERVICES.includes(name)) return;
      const state = parts.slice(2).join(".");
      const u = (f.payload as { has_u2d2?: boolean })?.has_u2d2;
      setRows((r) => ({
        ...r,
        [name]: {
          ...(r[name] ?? { name, lastSeenMs: null }),
          state,
          hasU2D2: typeof u === "boolean" ? u : r[name]?.hasU2D2,
        },
      }));
    });

    // One-shot snapshot seeds the `state` field for services that haven't
    // emitted a fresh state event since the page loaded. We intentionally do
    // NOT seed `lastSeenMs` from the snapshot — its `last_seen` is in server
    // wall-clock units and converting it cleanly requires sync logic we don't
    // need. The first live frame (≤ 5 s away) sets lastSeenMs correctly.
    void api.snapshot()
      .then((snap: any) => {
        const services = (snap.services ?? {}) as Record<string, { state?: string }>;
        setRows((r) => {
          const next = { ...r };
          for (const [name, info] of Object.entries(services)) {
            if (!KNOWN_SERVICES.includes(name)) continue;
            if (info.state) {
              next[name] = {
                ...(r[name] ?? { name, lastSeenMs: null }),
                state: r[name]?.state ?? info.state,
              };
            }
          }
          return next;
        });
      })
      .catch(() => undefined);

    interval = window.setInterval(() => setTick(Date.now()), TICK_MS);
  });

  onCleanup(() => {
    if (interval) clearInterval(interval);
    unsubAll?.();
    unsubState?.();
    unsubConn?.();
  });

  /** Three-valued liveness: true=online, false=offline, null=never seen.
   *
   *  Control is a special case — its liveness IS the WS connection. The
   *  browser is talking to control via this connection right now, so when
   *  the WS is open, control is unambiguously alive (no clock math, no
   *  jitter window). When the WS is closed we can't see anything anyway. */
  const liveness = (r: Row): boolean | null => {
    if (r.name === "control") return wsConnected();
    if (r.lastSeenMs === null) return null;
    return tick() - r.lastSeenMs < OFFLINE_AFTER_MS;
  };

  const dotColor = (r: Row): string => {
    const live = liveness(r);
    if (live === null) return "var(--c-stone)";
    if (!live) return "var(--c-coral)";
    if (r.state && ERROR_STATES.has(r.state)) return "var(--c-coral)";
    if (r.state && BUSY_STATES.has(r.state)) return "var(--c-amber)";
    return "var(--c-moss)";
  };

  const livenessLabel = (r: Row): { text: string; color: string } => {
    const live = liveness(r);
    if (live === null) return { text: "no signal", color: "var(--c-stone)" };
    if (live) return { text: "online", color: "var(--c-moss)" };
    return { text: "offline", color: "var(--c-coral)" };
  };

  const onRestart = async (name: string) => {
    setRestarting(name);
    try {
      await api.restartService(name);
      toast.ok(`restarting ${name}`, "watch for the dot to come back green");
    } catch (e: any) {
      toast.err(`restart ${name} failed`, e.message);
    } finally {
      setRestarting(null);
    }
  };

  const sortedRows = () =>
    Object.values(rows()).sort((a, b) => a.name.localeCompare(b.name));

  return (
    <Panel title="Services" eyebrow="lifecycle · heartbeats" accent="var(--c-moss)">
      <div
        style={{
          display: "flex",
          "flex-wrap": "wrap",
          gap: "8px",
        }}
      >
        <For each={sortedRows()}>
          {(r) => {
            const live = () => liveness(r);
            const showNoHardware = () =>
              r.name === "animator" && live() === true && r.hasU2D2 === false;
            const liveBadge = () => livenessLabel(r);
            const tooltip = () =>
              r.lastSeenMs === null
                ? `${r.name} · no frames seen yet`
                : `${r.name} · last frame ${Math.max(0, Math.floor((tick() - r.lastSeenMs!) / 1000))}s ago`;
            return (
              <div
                style={{
                  display: "flex",
                  "align-items": "center",
                  gap: "8px",
                  padding: "6px 10px 6px 12px",
                  "border-radius": "999px",
                  background: "rgba(243, 236, 220, 0.025)",
                  border: "1px solid var(--c-edge)",
                  transition: "opacity var(--t-base)",
                  flex: "1 1 180px",
                  "min-width": "0",
                  opacity: live() === false ? 0.6 : 1,
                }}
                title={tooltip()}
              >
                <span
                  style={{
                    width: "8px",
                    height: "8px",
                    "border-radius": "50%",
                    background: dotColor(r),
                    "box-shadow": live() === true ? `0 0 8px ${dotColor(r)}` : "none",
                    "flex-shrink": 0,
                    animation: live() === true ? "breathe 2.4s ease-in-out infinite" : "none",
                  }}
                />
                <span
                  class="f-display-roman"
                  style={{
                    "font-size": "14px",
                    color: "var(--c-bone)",
                    "min-width": "0",
                  }}
                >
                  {r.name}
                </span>
                <Show
                  when={r.state}
                  fallback={<span style={{ flex: "1 1 auto" }} />}
                >
                  <span
                    class="f-mono"
                    style={{
                      "font-size": "10px",
                      color: dotColor(r),
                      "letter-spacing": ".04em",
                      "white-space": "nowrap",
                      overflow: "hidden",
                      "text-overflow": "ellipsis",
                      flex: "1 1 auto",
                    }}
                  >
                    {r.state}
                  </span>
                </Show>
                <Show when={showNoHardware()}>
                  <span
                    class="f-mono"
                    style={{
                      "font-size": "9px",
                      color: "var(--c-amber)",
                      background: "rgba(212,162,89,.12)",
                      border: "1px solid rgba(212,162,89,.35)",
                      padding: "1px 6px",
                      "border-radius": "999px",
                      "letter-spacing": ".04em",
                      "white-space": "nowrap",
                    }}
                    title="animator is running but no U2D2 / servos are attached"
                  >
                    no hw
                  </span>
                </Show>
                <span
                  class="f-mono"
                  style={{
                    "font-size": "9px",
                    color: liveBadge().color,
                    "letter-spacing": ".05em",
                    "white-space": "nowrap",
                  }}
                >
                  {liveBadge().text}
                </span>
                <button
                  class="btn btn--ghost btn--micro"
                  style={{ padding: "2px 6px", "font-size": "10px" }}
                  disabled={restarting() === r.name}
                  onClick={() => onRestart(r.name)}
                  title={`Restart ${r.name}`}
                >
                  {restarting() === r.name ? "…" : "↻"}
                </button>
              </div>
            );
          }}
        </For>
      </div>
    </Panel>
  );
};
