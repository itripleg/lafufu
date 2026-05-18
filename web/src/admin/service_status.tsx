import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";

interface ServiceRow {
  name: string;
  last_seen: number;
  uptime_s: number;
  state?: string;
  lifecycle?: string; // starting | ready | restarting | stopped
}

const KNOWN_SERVICES = ["agent", "animator", "printer", "control"];

export const ServiceStatus: Component<{ nats: NatsWs }> = (props) => {
  const [rows, setRows] = createSignal<Record<string, ServiceRow>>({});
  const [now, setNow] = createSignal(Date.now() / 1000);

  let interval: number | undefined;
  let unsubHb: (() => void) | undefined;
  let unsubState: (() => void) | undefined;
  let unsubSvc: (() => void) | undefined;

  const ensureRow = (rs: Record<string, ServiceRow>, name: string): ServiceRow =>
    rs[name] ?? { name, last_seen: 0, uptime_s: 0 };

  onMount(async () => {
    // Pre-seed all known services so they appear even before first heartbeat.
    const seeded: Record<string, ServiceRow> = {};
    for (const name of KNOWN_SERVICES) {
      seeded[name] = { name, last_seen: 0, uptime_s: 0 };
    }
    try {
      const snap = await api.snapshot();
      for (const [name, info] of Object.entries(snap.services ?? {})) {
        seeded[name] = { ...seeded[name], ...(info as any), name };
      }
    } catch {
      /* ignore */
    }
    setRows(seeded);

    unsubHb = props.nats.subscribe("system.heartbeat.*", (f) => {
      const name = f.topic.split(".").pop()!;
      setRows((r) => ({
        ...r,
        [name]: {
          ...ensureRow(r, name),
          last_seen: Date.now() / 1000,
          uptime_s: f.payload.uptime_s,
        },
      }));
    });

    // Service-specific operational state (animator.state.idle, agent.state.listening, etc.)
    unsubState = props.nats.subscribe("*.state.*", (f) => {
      const parts = f.topic.split(".");
      if (parts[1] !== "state") return; // skip e.g. agent.tts.rms
      const name = parts[0];
      const state = parts.slice(2).join(".");
      setRows((r) => ({ ...r, [name]: { ...ensureRow(r, name), state } }));
    });

    // Lifecycle (BaseService publishes for every service, including control)
    unsubSvc = props.nats.subscribe("system.service.*", (f) => {
      const lifecycle = f.topic.split(".").pop()!;
      const name = f.payload?.service;
      if (!name) return;
      setRows((r) => ({ ...r, [name]: { ...ensureRow(r, name), lifecycle } }));
    });

    interval = window.setInterval(() => setNow(Date.now() / 1000), 1000);
  });
  onCleanup(() => {
    if (interval) clearInterval(interval);
    unsubHb?.();
    unsubState?.();
    unsubSvc?.();
  });

  const ageColor = (age: number, lastSeen: number) =>
    lastSeen === 0
      ? "bg-slate-600"
      : age < 10
      ? "bg-emerald-500"
      : age < 20
      ? "bg-amber-500"
      : "bg-red-500";

  // Choose the most informative label: operational state > lifecycle > "—"
  const stateLabel = (r: ServiceRow): string => r.state ?? r.lifecycle ?? "—";

  const stateClass = (r: ServiceRow): string => {
    if (r.state === "degraded" || r.state === "error" || r.state === "offline") {
      return "text-red-400";
    }
    if (r.state === "speaking" || r.state === "listening" || r.state === "thinking") {
      return "text-amber-300";
    }
    if (r.state === "idle" || r.lifecycle === "ready") return "text-emerald-300";
    if (r.lifecycle === "starting" || r.lifecycle === "restarting") return "text-amber-400";
    return "text-slate-400";
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Services</h2>
      <table class="w-full text-sm">
        <thead class="text-slate-400 text-xs uppercase">
          <tr>
            <th class="text-left pb-2">name</th>
            <th class="text-left">state</th>
            <th class="text-left">heartbeat</th>
            <th class="text-left">uptime</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <For each={Object.values(rows()).sort((a, b) => a.name.localeCompare(b.name))}>
            {(r) => {
              const age = now() - r.last_seen;
              return (
                <tr class="border-t border-slate-800">
                  <td class="py-2 font-mono">{r.name}</td>
                  <td class={stateClass(r)}>{stateLabel(r)}</td>
                  <td>
                    <span
                      class={`inline-block w-2 h-2 rounded-full mr-2 ${ageColor(
                        age,
                        r.last_seen,
                      )}`}
                    />
                    <span class="text-slate-400">
                      {r.last_seen === 0 ? "no heartbeat yet" : `${age.toFixed(0)}s ago`}
                    </span>
                  </td>
                  <td class="text-slate-400">{(r.uptime_s ?? 0).toFixed(0)}s</td>
                  <td>
                    <button
                      class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600"
                      onClick={() => api.restartService(r.name).catch((e) => alert(e.message))}
                    >
                      restart
                    </button>
                  </td>
                </tr>
              );
            }}
          </For>
        </tbody>
      </table>
    </section>
  );
};
