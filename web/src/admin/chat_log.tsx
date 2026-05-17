import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";

interface Entry { role: "user" | "lafufu" | "system"; text: string; emotion?: string }

export const ChatLog: Component<{ nats: NatsWs }> = (props) => {
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [input, setInput] = createSignal("");

  let unsubT: (() => void) | undefined;
  let unsubR: (() => void) | undefined;

  onMount(() => {
    unsubT = props.nats.subscribe("agent.transcript", (f) => {
      setEntries((e) => [...e.slice(-50), { role: "user", text: f.payload.text }]);
    });
    unsubR = props.nats.subscribe("agent.reply", (f) => {
      setEntries((e) => [...e.slice(-50), { role: "lafufu", text: f.payload.text, emotion: f.payload.emotion }]);
    });
  });
  onCleanup(() => { unsubT?.(); unsubR?.(); });

  const send = async () => {
    const text = input().trim();
    if (!text) return;
    setInput("");
    await api.agentTextMessage(text);
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4 flex flex-col h-[60vh]">
      <h2 class="text-lg font-semibold mb-3">Chat</h2>
      <div class="flex-1 overflow-y-auto space-y-2 mb-3">
        <For each={entries()}>{(e) => (
          <div class={`text-sm ${e.role === "user" ? "text-slate-300" : "text-emerald-300"}`}>
            <span class="font-mono text-xs opacity-60">{e.role}{e.emotion ? `:${e.emotion}` : ""}</span>
            <div>{e.text}</div>
          </div>
        )}</For>
      </div>
      <div class="flex gap-2">
        <input
          class="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
          placeholder="Send text to Lafufu..."
          value={input()}
          onInput={(e) => setInput(e.currentTarget.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button class="text-sm px-3 rounded bg-emerald-600 hover:bg-emerald-500" onClick={send}>send</button>
      </div>
    </section>
  );
};
