import { Component, createSignal, onCleanup, onMount, For, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { EMOTION_COLORS, EMOTION_GLYPH, type Emotion } from "../shared/design";
import { lsGet, lsSet } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

interface Entry {
  role: "user" | "lafufu" | "puppet";
  text: string;
  emotion?: string;
  ts: number;
  /** Round-trip time (ms) from chat send to this reply. Only set on lafufu replies. */
  elapsedMs?: number;
}

type Tab = "chat" | "speak";

const EMOTIONS = Object.keys(EMOTION_COLORS) as Emotion[];

const DRAFT_INPUTS = "chat/inputs";

/** Format a millisecond duration as a compact "1.2s" / "12s" string. */
const fmtElapsed = (ms: number): string => {
  const s = ms / 1000;
  return s < 10 ? `${s.toFixed(1)}s` : `${Math.round(s)}s`;
};

const DEFAULT_PUPPET =
  "Hello! I'm Lafufu, a little mischievous creature. " +
  "If you can hear me clearly through the speaker right now, then the " +
  "whole voice pipeline is working end to end. Try changing my emotion " +
  "with the dropdown and sending this again — my expression should shift " +
  "to match. Pretty neat, right?";

export const ChatLog: Component<{ nats: NatsWs }> = (props) => {
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [tab, setTab] = createSignal<Tab>("chat");
  const [chatInput, setChatInput] = createSignal("");
  const [speakInput, setSpeakInput] = createSignal(DEFAULT_PUPPET);
  const [speakEmotion, setSpeakEmotion] = createSignal<Emotion>("neutral");
  const [sending, setSending] = createSignal(false);
  // Round-trip timer: set to Date.now() when a chat message is sent, cleared
  // when its reply lands. nowTick drives the live counter while pending.
  const [pendingSince, setPendingSince] = createSignal<number | null>(null);
  const [nowTick, setNowTick] = createSignal(Date.now());
  let scrollEl!: HTMLDivElement;

  let unsubT: (() => void) | undefined;
  let unsubR: (() => void) | undefined;
  let tickTimer: number | undefined;

  const appendDedup = (entry: Omit<Entry, "ts">) => {
    setEntries((e) => {
      const last = e[e.length - 1];
      const now = Date.now();
      if (last && last.role === entry.role && last.text === entry.text && now - last.ts < 500) {
        return e;
      }
      return [...e.slice(-99), { ...entry, ts: now }];
    });
    queueMicrotask(() => { if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight; });
  };

  onMount(() => {
    // Hydrate drafted inputs (so a refresh doesn't lose the puppet text)
    const cached = lsGet<{ chat?: string; speak?: string; emotion?: Emotion }>(DRAFT_INPUTS, {});
    if (cached.chat)    setChatInput(cached.chat);
    if (cached.speak)   setSpeakInput(cached.speak);
    if (cached.emotion) setSpeakEmotion(cached.emotion);

    unsubT = props.nats.subscribe("agent.transcript", (f) => {
      appendDedup({ role: "user", text: f.payload.text });
    });
    unsubR = props.nats.subscribe("agent.reply", (f) => {
      const role: Entry["role"] = f.payload.source === "puppet" ? "puppet" : "lafufu";
      // Stamp the round-trip time if this reply answers a pending chat send.
      let elapsedMs: number | undefined;
      const since = pendingSince();
      if (role === "lafufu" && since !== null) {
        elapsedMs = Date.now() - since;
        setPendingSince(null);
      }
      appendDedup({ role, text: f.payload.text, emotion: f.payload.emotion, elapsedMs });
    });

    // Tick the live round-trip counter ~10x/s while a reply is pending.
    tickTimer = window.setInterval(() => {
      if (pendingSince() !== null) setNowTick(Date.now());
    }, 100);
  });
  onCleanup(() => {
    unsubT?.();
    unsubR?.();
    if (tickTimer) clearInterval(tickTimer);
    // Persist inputs on unmount
    lsSet(DRAFT_INPUTS, {
      chat:    chatInput(),
      speak:   speakInput(),
      emotion: speakEmotion(),
    });
  });

  const sendChat = async () => {
    const text = chatInput().trim();
    if (!text || sending()) return;
    setSending(true);
    setChatInput("");
    setPendingSince(Date.now()); // start the round-trip timer
    lsSet(DRAFT_INPUTS, { chat: "", speak: speakInput(), emotion: speakEmotion() });
    try {
      await api.agentTextMessage(text);
    } catch (err: any) {
      toast.err("chat failed", err.message);
      setChatInput(text); // restore so user can retry
      setPendingSince(null); // send failed — cancel the timer
    } finally {
      setSending(false);
    }
  };

  const sendSpeak = async () => {
    const text = speakInput().trim();
    if (!text || sending()) return;
    setSending(true);
    try {
      await api.agentSpeakText(text, speakEmotion());
      toast.ok(`speaking · ${speakEmotion()}`);
    } catch (err: any) {
      toast.err("speak failed", err.message);
    } finally {
      setSending(false);
    }
  };

  const handleTextareaKeyDown = (e: KeyboardEvent & { currentTarget: HTMLTextAreaElement }) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendSpeak(); return; }
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

  const onSpeakInput = (v: string) => {
    setSpeakInput(v);
    lsSet(DRAFT_INPUTS, { chat: chatInput(), speak: v, emotion: speakEmotion() });
  };
  const onChatInput = (v: string) => {
    setChatInput(v);
    lsSet(DRAFT_INPUTS, { chat: v, speak: speakInput(), emotion: speakEmotion() });
  };
  const onEmotionChange = (e: Emotion) => {
    setSpeakEmotion(e);
    lsSet(DRAFT_INPUTS, { chat: chatInput(), speak: speakInput(), emotion: e });
  };

  const roleColor = (role: Entry["role"]) =>
    role === "user"   ? "var(--c-mist)"
    : role === "puppet" ? "var(--c-amber)"
    : "var(--c-moss)";

  return (
    <Panel
      title="Chat"
      eyebrow="agent.transcript · agent.reply"
      accent="var(--c-moss)"
      fullHeight
      style={{ "min-height": "62vh", display: "flex", "flex-direction": "column" }}
      actions={
        <div
          style={{
            display: "flex",
            background: "var(--c-shell)",
            "border-radius": "10px",
            border: "1px solid var(--c-edge)",
            padding: "2px",
            gap: "1px",
          }}
        >
          <button
            class="btn btn--micro"
            style={{
              background: tab() === "chat" ? "var(--c-raised)" : "transparent",
              border: "none",
            }}
            onClick={() => setTab("chat")}
            title="Send text as user input → LLM generates reply"
          >
            chat
          </button>
          <button
            class="btn btn--micro"
            style={{
              background: tab() === "speak" ? "var(--c-raised)" : "transparent",
              border: "none",
            }}
            onClick={() => setTab("speak")}
            title="Type exactly what Lafufu should say (skips LLM)"
          >
            puppet
          </button>
        </div>
      }
    >
      <div
        ref={scrollEl}
        class="scroll-warm"
        style={{
          flex: 1,
          "overflow-y": "auto",
          "padding-right": "6px",
          "margin-bottom": "14px",
          display: "flex",
          "flex-direction": "column",
          /* When messages don't fill the panel, push them to the bottom edge
             (just above the input) so the empty space appears above instead
             of awkwardly between the last message and the input. Standard
             chat-app feel — messages "rise" from the input as they arrive. */
          "justify-content": "flex-end",
          gap: "10px",
          "min-height": "180px",
        }}
      >
        <For each={entries()}>
          {(e) => (
            <div
              style={{
                "align-self": e.role === "user" ? "flex-end" : "flex-start",
                "max-width": "82%",
                padding: "8px 12px",
                "border-radius": e.role === "user" ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
                background: e.role === "user" ? "var(--c-raised)" : "rgba(149,176,122,0.10)",
                border: "1px solid var(--c-edge)",
                color: "var(--c-bone)",
                "font-size": "13px",
                "line-height": 1.4,
                animation: "fade-up .3s cubic-bezier(.2,.7,.3,1.1) both",
              }}
            >
              <div
                class="f-mono"
                style={{
                  "font-size": "10px",
                  color: roleColor(e.role),
                  "margin-bottom": "2px",
                  "letter-spacing": ".05em",
                  display: "flex",
                  "align-items": "center",
                  gap: "6px",
                }}
              >
                <span>{e.role}</span>
                <Show when={e.emotion}>
                  <span style={{ color: EMOTION_COLORS[e.emotion as Emotion] ?? "var(--c-mist)" }}>
                    {EMOTION_GLYPH[e.emotion as Emotion]} {e.emotion}
                  </span>
                </Show>
                <Show when={e.elapsedMs !== undefined}>
                  <span
                    title="round-trip time: send to reply"
                    style={{ color: "var(--c-stone)", "margin-left": "auto" }}
                  >
                    ⧗ {fmtElapsed(e.elapsedMs!)}
                  </span>
                </Show>
              </div>
              <div style={{ "white-space": "pre-wrap", "word-break": "break-word" }}>
                {e.text}
              </div>
            </div>
          )}
        </For>
        <Show when={pendingSince()}>
          {(since) => (
            <div
              style={{
                "align-self": "flex-start",
                "max-width": "82%",
                padding: "8px 12px",
                "border-radius": "14px 14px 14px 4px",
                background: "rgba(149,176,122,0.10)",
                border: "1px solid var(--c-edge)",
                animation: "fade-up .3s cubic-bezier(.2,.7,.3,1.1) both",
              }}
            >
              <span
                class="f-mono"
                style={{
                  "font-size": "10px",
                  color: "var(--c-moss)",
                  "letter-spacing": ".05em",
                }}
              >
                lafufu · thinking… ⧗ {fmtElapsed(nowTick() - since())}
              </span>
            </div>
          )}
        </Show>
        <Show when={entries().length === 0 && pendingSince() === null}>
          <div
            style={{
              color: "var(--c-stone)",
              "font-style": "italic",
              "font-family": "var(--f-display)",
              "text-align": "center",
              "margin": "auto 0",
            }}
          >
            no messages yet — start a conversation below
          </div>
        </Show>
      </div>

      <Show when={tab() === "chat"}>
        <div style={{ display: "flex", gap: "8px" }}>
          <input
            class="field"
            style={{ flex: 1, "font-family": "var(--f-sans)" }}
            placeholder="ask lafufu something…"
            value={chatInput()}
            disabled={sending()}
            onInput={(e) => onChatInput(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendChat()}
          />
          <button
            class="btn btn--primary"
            disabled={sending() || !chatInput().trim()}
            onClick={sendChat}
          >
            send
          </button>
        </div>
      </Show>

      <Show when={tab() === "speak"}>
        <div style={{ display: "flex", "flex-direction": "column", gap: "8px" }}>
          <textarea
            class="field"
            rows="4"
            style={{ "font-family": "var(--f-sans)", resize: "vertical" }}
            placeholder="exactly what lafufu should say… (⌘+Enter to send, Tab inserts tab)"
            value={speakInput()}
            disabled={sending()}
            onInput={(e) => onSpeakInput(e.currentTarget.value)}
            onKeyDown={handleTextareaKeyDown}
          />
          <div style={{ display: "flex", gap: "8px", "align-items": "center" }}>
            <label
              class="f-mono"
              style={{ "font-size": "11px", color: "var(--c-stone)", "letter-spacing": ".05em" }}
            >
              emotion
            </label>
            <select
              class="field"
              style={{ "font-family": "var(--f-sans)", "text-transform": "capitalize" }}
              value={speakEmotion()}
              onChange={(e) => onEmotionChange(e.currentTarget.value as Emotion)}
            >
              <For each={EMOTIONS}>{(em) => <option value={em}>{em}</option>}</For>
            </select>
            <div style={{ flex: 1 }} />
            <button
              class="btn btn--primary"
              disabled={sending() || !speakInput().trim()}
              onClick={sendSpeak}
              style={{
                background: `linear-gradient(180deg, ${EMOTION_COLORS[speakEmotion()]} 0%, ${EMOTION_COLORS[speakEmotion()]}cc 100%)`,
                color: "#1a1410",
              }}
            >
              speak it
            </button>
          </div>
        </div>
      </Show>
    </Panel>
  );
};
