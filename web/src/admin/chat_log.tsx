import { Component, createSignal, onCleanup, onMount, For, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { EMOTION_COLORS } from "../shared/design";

interface Entry {
  role: "user" | "lafufu" | "puppet";
  text: string;
  emotion?: string;
  ts: number; // for dedupe + ordering
}

type Tab = "chat" | "speak";

const EMOTIONS = Object.keys(EMOTION_COLORS) as Array<keyof typeof EMOTION_COLORS>;

export const ChatLog: Component<{ nats: NatsWs }> = (props) => {
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [tab, setTab] = createSignal<Tab>("chat");
  const DEFAULT_PUPPET = (
    "Hello! I'm Lafufu, a little mischievous creature. " +
    "If you can hear me clearly through the speaker right now, then the " +
    "whole voice pipeline is working end to end. Try changing my emotion " +
    "with the dropdown and sending this again — my expression should shift " +
    "to match. Pretty neat, right?"
  );
  const [chatInput, setChatInput] = createSignal("");
  const [speakInput, setSpeakInput] = createSignal(DEFAULT_PUPPET);
  const [speakEmotion, setSpeakEmotion] = createSignal<string>("neutral");
  const [sending, setSending] = createSignal(false);

  let unsubT: (() => void) | undefined;
  let unsubR: (() => void) | undefined;

  // Defensive dedupe: if the same role+text arrives within 500ms (e.g. WS
  // reconnect re-deliver), drop the second.
  const appendDedup = (entry: Omit<Entry, "ts">) => {
    setEntries((e) => {
      const last = e[e.length - 1];
      const now = Date.now();
      if (
        last &&
        last.role === entry.role &&
        last.text === entry.text &&
        now - last.ts < 500
      ) {
        return e;
      }
      return [...e.slice(-99), { ...entry, ts: now }];
    });
  };

  onMount(() => {
    unsubT = props.nats.subscribe("agent.transcript", (f) => {
      appendDedup({ role: "user", text: f.payload.text });
    });
    unsubR = props.nats.subscribe("agent.reply", (f) => {
      const role: Entry["role"] = f.payload.source === "puppet" ? "puppet" : "lafufu";
      appendDedup({ role, text: f.payload.text, emotion: f.payload.emotion });
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
    // No local echo — agent.reply (with source="puppet") will arrive and be
    // rendered as a "puppet" entry by the subscriber. Avoids the double-post
    // we used to have. Textarea stays populated so operator can re-send.
    try {
      await api.agentSpeakText(text, speakEmotion());
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSending(false);
    }
  };

  // Insert literal Tab in the textarea instead of moving focus.
  const handleTextareaKeyDown = (e: KeyboardEvent & { currentTarget: HTMLTextAreaElement }) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendSpeak();
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      const ta = e.currentTarget;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const v = ta.value;
      ta.value = v.slice(0, start) + "\t" + v.slice(end);
      ta.selectionStart = ta.selectionEnd = start + 1;
      setSpeakInput(ta.value);
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
            class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm resize-y font-sans leading-relaxed"
            rows="5"
            placeholder="Type exactly what Lafufu should say (long text OK; Tab inserts a tab; ctrl+Enter to send)..."
            value={speakInput()}
            disabled={sending()}
            onInput={(e) => setSpeakInput(e.currentTarget.value)}
            onKeyDown={handleTextareaKeyDown}
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
