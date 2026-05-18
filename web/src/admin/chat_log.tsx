import { Component, createSignal, onCleanup, onMount, For, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { EMOTION_COLORS } from "../shared/design";

interface Entry {
  role: "user" | "lafufu" | "puppet";
  text: string;
  emotion?: string;
}

type Tab = "chat" | "speak";

const EMOTIONS = Object.keys(EMOTION_COLORS) as Array<keyof typeof EMOTION_COLORS>;

export const ChatLog: Component<{ nats: NatsWs }> = (props) => {
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [tab, setTab] = createSignal<Tab>("chat");
  const [chatInput, setChatInput] = createSignal("");
  const [speakInput, setSpeakInput] = createSignal("");
  const [speakEmotion, setSpeakEmotion] = createSignal<string>("neutral");
  const [sending, setSending] = createSignal(false);

  let unsubT: (() => void) | undefined;
  let unsubR: (() => void) | undefined;

  onMount(() => {
    unsubT = props.nats.subscribe("agent.transcript", (f) => {
      setEntries((e) => [...e.slice(-99), { role: "user", text: f.payload.text }]);
    });
    unsubR = props.nats.subscribe("agent.reply", (f) => {
      setEntries((e) => [
        ...e.slice(-99),
        { role: "lafufu", text: f.payload.text, emotion: f.payload.emotion },
      ]);
    });
  });
  onCleanup(() => {
    unsubT?.();
    unsubR?.();
  });

  const sendChat = async () => {
    const text = chatInput().trim();
    if (!text || sending()) return;
    setSending(true);
    setChatInput("");
    try {
      await api.agentTextMessage(text);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSending(false);
    }
  };

  const sendSpeak = async () => {
    const text = speakInput().trim();
    if (!text || sending()) return;
    setSending(true);
    // Echo to local log as a "puppet" entry so the user sees what they sent
    setEntries((e) => [
      ...e.slice(-99),
      { role: "puppet", text, emotion: speakEmotion() },
    ]);
    setSpeakInput("");
    try {
      await api.agentSpeakText(text, speakEmotion());
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSending(false);
    }
  };

  const roleColor = (role: Entry["role"]) =>
    role === "user"
      ? "text-slate-300"
      : role === "puppet"
      ? "text-amber-300"
      : "text-emerald-300";

  return (
    <section class="rounded-lg bg-slate-900 p-4 flex flex-col h-[60vh]">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold">Chat</h2>
        <div class="flex gap-1 text-xs">
          <button
            class={`px-2 py-1 rounded ${
              tab() === "chat" ? "bg-slate-700 text-slate-100" : "text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("chat")}
            title="Send text as user input — LLM generates Lafufu's reply"
          >
            chat
          </button>
          <button
            class={`px-2 py-1 rounded ${
              tab() === "speak" ? "bg-slate-700 text-slate-100" : "text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("speak")}
            title="Type exactly what Lafufu should say — skips the LLM"
          >
            speak (puppet)
          </button>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto space-y-2 mb-3 pr-1">
        <For each={entries()}>
          {(e) => (
            <div class={`text-sm ${roleColor(e.role)}`}>
              <span class="font-mono text-xs opacity-60">
                {e.role}
                {e.emotion ? `:${e.emotion}` : ""}
              </span>
              <div class="whitespace-pre-wrap break-words">{e.text}</div>
            </div>
          )}
        </For>
      </div>

      <Show when={tab() === "chat"}>
        <div class="flex gap-2">
          <input
            class="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
            placeholder="Send text to Lafufu — she'll think + reply..."
            value={chatInput()}
            disabled={sending()}
            onInput={(e) => setChatInput(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendChat()}
          />
          <button
            class="text-sm px-3 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40"
            disabled={sending()}
            onClick={sendChat}
          >
            send
          </button>
        </div>
      </Show>

      <Show when={tab() === "speak"}>
        <div class="space-y-2">
          <textarea
            class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm resize-y"
            rows="3"
            placeholder="Type exactly what Lafufu should say (long text OK; ctrl+Enter to send)..."
            value={speakInput()}
            disabled={sending()}
            onInput={(e) => setSpeakInput(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendSpeak();
            }}
          />
          <div class="flex items-center gap-2">
            <label class="text-xs text-slate-400">emotion:</label>
            <select
              class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm capitalize"
              value={speakEmotion()}
              onChange={(e) => setSpeakEmotion(e.currentTarget.value)}
            >
              <For each={EMOTIONS}>{(em) => <option value={em}>{em}</option>}</For>
            </select>
            <div class="flex-1" />
            <button
              class="text-sm px-3 py-1 rounded bg-amber-600 hover:bg-amber-500 disabled:opacity-40"
              disabled={sending()}
              onClick={sendSpeak}
            >
              speak it
            </button>
          </div>
        </div>
      </Show>
    </section>
  );
};
