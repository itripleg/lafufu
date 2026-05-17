import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";

export const SystemPulse: Component<{ nats: NatsWs }> = (props) => {
  const [lines, setLines] = createSignal<{ ts: number; topic: string; payload: any }[]>([]);

  let unsub: (() => void) | undefined;
  onMount(() => {
    unsub = props.nats.subscribe(">", (f) => {
      setLines((ls) => [...ls.slice(-99), { ts: Date.now(), topic: f.topic, payload: f.payload }]);
    });
  });
  onCleanup(() => unsub?.());

  return (
    <section class="rounded-lg bg-slate-900 p-4 col-span-full">
      <h2 class="text-lg font-semibold mb-2">System pulse</h2>
      <div class="font-mono text-xs max-h-60 overflow-y-auto bg-slate-950 rounded p-2 space-y-0.5">
        <For each={lines()}>{(l) => (
          <div class="flex gap-2">
            <span class="text-slate-500 w-24 shrink-0">{new Date(l.ts).toLocaleTimeString()}</span>
            <span class="text-emerald-400 w-64 shrink-0 truncate">{l.topic}</span>
            <span class="text-slate-300 truncate">{JSON.stringify(l.payload)}</span>
          </div>
        )}</For>
      </div>
    </section>
  );
};
