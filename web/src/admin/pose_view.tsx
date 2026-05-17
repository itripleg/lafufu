import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";

const SERVOS = ["head_lr", "head_ud", "eye", "jaw", "brow"] as const;

export const PoseView: Component<{ nats: NatsWs }> = (props) => {
  const [pose, setPose] = createSignal<Record<string, number>>({});

  let unsub: (() => void) | undefined;
  onMount(() => {
    unsub = props.nats.subscribe("animator.pose", (f) => setPose(f.payload));
  });
  onCleanup(() => unsub?.());

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Live pose</h2>
      <div class="grid grid-cols-5 gap-3 text-center">
        <For each={SERVOS}>{(name) => (
          <div class="rounded bg-slate-800 p-2">
            <div class="text-xs uppercase text-slate-500">{name}</div>
            <div class="text-2xl font-mono tabular-nums">{pose()[name] ?? "—"}</div>
          </div>
        )}</For>
      </div>
    </section>
  );
};
