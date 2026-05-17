import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";

interface ServiceRow {
  name: string;
  last_seen: number;
  uptime_s: number;
  state?: string;
}

export const ServiceStatus: Component<{ nats: NatsWs }> = (props) => {
  const [rows, setRows] = createSignal<Record<string, ServiceRow>>({});
  const [now, setNow] = createSignal(Date.now() / 1000);

  let interval: number | undefined;
  let unsubHb: (() => void) | undefined;
  let unsubState: (() => void) | undefined;

  onMount(async () => {
    // Seed from snapshot
    try {
      const snap = await api.snapshot();
      const seeded: Record<string, ServiceRow> = {};
      for (const [name, info] of Object.entries(snap.services ?? {})) {
        seeded[name] = { name, ...(info as any) };
      }
      setRows(seeded);
    } catch { /* ignore */ }

    unsubHb = props.nats.subscribe("system.heartbeat.*", (f) => {
      const name = f.topic.split(".").pop()!;
      setRows((r) => ({
        ...r,
        [name]: { ...(r[name] ?? { name }), name, last_seen: Date.now() / 1000, uptime_s: f.payload.uptime_s },
      }));
    });
    unsubState = props.nats.subscribe("*.state.*", (f) => {
      const parts = f.topic.split(".");
      const name = parts[0];
      const state = parts.slice(2).join(".");
      setRows((r) => ({ ...r, [name]: { ...(r[name] ?? { name, last_seen: 0, uptime_s: 0 }), state } }));
    });

    interval = window.setInterval(() => setNow(Date.now() / 1000), 1000);
  });
  onCleanup(() => {
    if (interval) clearInterval(interval);
    unsubHb?.();
    unsubState?.();
  });

  const ageColor = (age: number) =>
    age < 10 ? "bg-emerald-500" : age < 20 ? "bg-amber-500" : "bg-red-500";

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Services</h2>
      <table class="w-full text-sm">
        <thead class="text-slate-400 text-xs uppercase">
          <tr><th class="text-left pb-2">name</th><th class="text-left">state</th><th class="text-left">heartbeat</th><th class="text-left">uptime</th><th></th></tr>
        </thead>
        <tbody>
          <For each={Object.values(rows()).sort((a, b) => a.name.localeCompare(b.name))}>{(r) => {
            const age = now() - r.last_seen;
            return (
              <tr class="border-t border-slate-800">
                <td class="py-2 font-mono">{r.name}</td>
                <td class="text-slate-300">{r.state ?? "?"}</td>
                <td>
                  <span class={`inline-block w-2 h-2 rounded-full mr-2 ${ageColor(age)}`} />
                  <span class="text-slate-400">{age.toFixed(0)}s ago</span>
                </td>
                <td class="text-slate-400">{(r.uptime_s ?? 0).toFixed(0)}s</td>
                <td>
                  <button
                    class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600"
                    onClick={() => api.restartService(r.name).catch((e) => alert(e.message))}
                  >restart</button>
                </td>
              </tr>
            );
          }}</For>
        </tbody>
      </table>
    </section>
  );
};
