import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

interface ServiceRow {
  name: string;
  last_seen: number | null;
  uptime_s: number;
  state?: string;
  lifecycle?: string;
}

const KNOWN_SERVICES = ["agent", "animator", "printer", "control"];

export const ServiceStatus: Component<{ nats: NatsWs }> = (props) => {
  const [rows, setRows] = createSignal<Record<string, ServiceRow>>({});
  const [serverNow, setServerNow] = createSignal(0);
  const [clientAtSeed, setClientAtSeed] = createSignal(0);
  const [tick, setTick] = createSignal(Date.now() / 1000);
  const [restarting, setRestarting] = createSignal<string | null>(null);

  let interval: number | undefined;
  let unsubHb: (() => void) | undefined;
  let unsubState: (() => void) | undefined;
  let unsubSvc: (() => void) | undefined;

  const ensureRow = (rs: Record<string, ServiceRow>, name: string): ServiceRow =>
    rs[name] ?? { name, last_seen: null, uptime_s: 0 };

  const ageSeconds = (lastSeen: number | null): number | null => {
    if (lastSeen === null || lastSeen === 0) return null;
    if (serverNow() === 0) return null;
    const elapsedSinceSeed = tick() - clientAtSeed();
    return serverNow() + elapsedSinceSeed - lastSeen;
  };

  onMount(async () => {
    const seeded: Record<string, ServiceRow> = {};
    for (const name of KNOWN_SERVICES) {
      seeded[name] = { name, last_seen: null, uptime_s: 0 };
    }
    try {
      const snap: any = await api.snapshot();
      setServerNow(snap.server_now ?? Date.now() / 1000);
      setClientAtSeed(Date.now() / 1000);
      for (const [name, info] of Object.entries(snap.services ?? {})) {
        seeded[name] = { ...seeded[name], ...(info as any), name };
      }
    } catch {
      setServerNow(Date.now() / 1000);
      setClientAtSeed(Date.now() / 1000);
    }
    setRows(seeded);

    const serverNowLive = () =>
      serverNow() + (Date.now() / 1000 - clientAtSeed());

    unsubHb = props.nats.subscribe("system.heartbeat.*", (f) => {
      const name = f.topic.split(".").pop()!;
      setRows((r) => ({
        ...r,
        [name]: {
          ...ensureRow(r, name),
          last_seen: serverNowLive(),
          uptime_s: f.payload.uptime_s,
        },
      }));
    });

    unsubState = props.nats.subscribe("*.state.*", (f) => {
      const parts = f.topic.split(".");
      if (parts[1] !== "state") return;
      const name = parts[0];
      const state = parts.slice(2).join(".");
      setRows((r) => ({ ...r, [name]: { ...ensureRow(r, name), state } }));
    });

    unsubSvc = props.nats.subscribe("system.service.*", (f) => {
      const lifecycle = f.topic.split(".").pop()!;
      const name = f.payload?.service;
      if (!name) return;
      setRows((r) => ({ ...r, [name]: { ...ensureRow(r, name), lifecycle } }));
    });

    interval = window.setInterval(() => setTick(Date.now() / 1000), 1000);
  });

  onCleanup(() => {
    if (interval) clearInterval(interval);
    unsubHb?.();
    unsubState?.();
    unsubSvc?.();
  });

  const ageColor = (age: number | null) => {
    if (age === null) return "var(--c-stone)";
    const a = Math.max(0, age);
    return a < 10 ? "var(--c-moss)" : a < 20 ? "var(--c-amber)" : "var(--c-coral)";
  };

  const ageLabel = (age: number | null) => {
    if (age === null) return "—";
    return `${Math.max(0, age).toFixed(0)}s`;
  };

  const stateLabel = (r: ServiceRow): string => r.state ?? r.lifecycle ?? "—";
  const stateColor = (r: ServiceRow): string => {
    if (r.state === "degraded" || r.state === "error" || r.state === "offline") return "var(--c-coral)";
    if (r.state === "speaking" || r.state === "listening" || r.state === "thinking") return "var(--c-amber)";
    if (r.state === "idle" || r.lifecycle === "ready") return "var(--c-moss)";
    if (r.lifecycle === "starting" || r.lifecycle === "restarting") return "var(--c-amber)";
    return "var(--c-mist)";
  };

  const onRestart = async (name: string) => {
    setRestarting(name);
    try {
      await api.restartService(name);
      toast.ok(`restarting ${name}`, "watch for the heartbeat to come back green");
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
            const age = () => ageSeconds(r.last_seen);
            const c = () => stateColor(r);
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
                  transition: "background var(--t-fast)",
                  flex: "1 1 180px",
                  "min-width": "0",
                }}
                title={`${r.name} · uptime ${(r.uptime_s ?? 0).toFixed(0)}s`}
              >
                <span
                  style={{
                    width: "8px", height: "8px",
                    "border-radius": "50%",
                    background: c(),
                    "box-shadow": `0 0 8px ${c()}`,
                    "flex-shrink": 0,
                    animation: "breathe 2.4s ease-in-out infinite",
                  }}
                />
                <span
                  class="f-display-roman"
                  style={{ "font-size": "14px", color: "var(--c-bone)", "min-width": "0" }}
                >
                  {r.name}
                </span>
                <span
                  class="f-mono"
                  style={{
                    "font-size": "10px",
                    color: c(),
                    "letter-spacing": ".04em",
                    "white-space": "nowrap",
                    overflow: "hidden",
                    "text-overflow": "ellipsis",
                    flex: "1 1 auto",
                  }}
                >
                  {stateLabel(r)}
                </span>
                <span
                  class="f-mono f-num"
                  style={{
                    "font-size": "10px",
                    color: ageColor(age()),
                    "white-space": "nowrap",
                  }}
                  title="seconds since last heartbeat"
                >
                  {ageLabel(age())}
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
