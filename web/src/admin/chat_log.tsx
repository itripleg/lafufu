import { Component, createMemo, createSignal, onCleanup, onMount, For, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { api, type ChatRow } from "../shared/api";
import { EMOTION_COLORS, type Emotion } from "../shared/design";
import { LafufuHead } from "../shared/lafufu_head";
import { lsGet, lsSet } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

export interface Entry {
  /** DB row id — absent for live (not-yet-persisted) entries. */
  id?: number;
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

// stateBadge moved to ../shared/agent_state — re-exported here so existing
// imports (including the unit tests) keep working.
export { stateBadge, type StateBadge } from "../shared/agent_state";
import { stateBadge } from "../shared/agent_state";

const rowToEntry = (r: ChatRow): Entry => {
  const hasTz = /(?:Z|[+-]\d\d:?\d\d)$/.test(r.created_at);
  return {
    id: r.id,
    role: r.role,
    text: r.text,
    emotion: r.emotion ?? undefined,
    ts: Date.parse(hasTz ? r.created_at : r.created_at + "Z"),
    elapsedMs: r.reply_delay_ms ?? undefined,
  };
};

/**
 * Merge loaded history in front of entries that arrived live during the fetch.
 * History is older so it goes first; a live entry that duplicates the tail of
 * history — the same turn fanned out live while the GET was in flight — is
 * dropped. The result keeps the most recent 100 entries.
 */
export const mergeHistory = (history: Entry[], live: Entry[]): Entry[] => {
  if (live.length === 0) return history.slice(-100);
  if (history.length === 0) return live.slice(-100);
  const tail = history[history.length - 1];
  const deduped =
    live[0].role === tail.role && live[0].text === tail.text ? live.slice(1) : live;
  return [...history, ...deduped].slice(-100);
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
  const [stage, setStage] = createSignal<{ name: string; since: number } | null>(null);
  // Always-on agent state — every agent.state.* event lands here so the
  // status badge can show "waiting for wake word" / "idle" / etc. continuously.
  const [agentState, setAgentState] = createSignal<string | null>(null);

  // The pipeline stage to show in the indicator: a live agent stage if one is
  // active, else a "thinking" placeholder while a widget chat send is pending.
  const activeStage = createMemo(() =>
    stage() ?? (pendingSince() !== null ? { name: "thinking", since: pendingSince()! } : null),
  );

  let scrollEl!: HTMLDivElement;

  let unsubT: (() => void) | undefined;
  let unsubR: (() => void) | undefined;
  let unsubS: (() => void) | undefined;
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
    // Subscribe to the live stream FIRST — before the history fetch below — so
    // a turn that completes while the GET is in flight isn't missed.
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
    unsubS = props.nats.subscribe("agent.state.*", (f) => {
      const name = String(f.payload?.state ?? "");
      setAgentState(name || null);
      // Only the working stages drive the in-flow stage bubble (with timer);
      // the persistent state badge above shows everything.
      if (name === "transcribing" || name === "thinking" || name === "speaking") {
        setStage((cur) => (cur?.name === name ? cur : { name, since: Date.now() }));
      } else {
        setStage(null);
      }
    });

    // Hydrate persisted history, merging it in front of any entries that
    // arrived live while the request was in flight. A failed load degrades
    // gracefully to live-only.
    void api
      .chatMessages()
      .then(({ messages }) => {
        const history = messages.map(rowToEntry);
        setEntries((live) => mergeHistory(history, live));
        queueMicrotask(() => { if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight; });
      })
      .catch((err) => {
        toast.err("chat history failed to load", err instanceof Error ? err.message : String(err));
      });

    // Hydrate drafted inputs (so a refresh doesn't lose the puppet text)
    const cached = lsGet<{ chat?: string; speak?: string; emotion?: Emotion }>(DRAFT_INPUTS, {});
    if (cached.chat)    setChatInput(cached.chat);
    if (cached.speak)   setSpeakInput(cached.speak);
    if (cached.emotion) setSpeakEmotion(cached.emotion);

    // Tick the live round-trip counter ~10x/s while a reply is pending or a stage is active.
    tickTimer = window.setInterval(() => {
      if (pendingSince() !== null || stage() !== null) setNowTick(Date.now());
    }, 100);
  });
  onCleanup(() => {
    unsubT?.();
    unsubR?.();
    unsubS?.();
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
      {/* Persistent agent-state badge — always visible so the operator can
          tell whether Lafufu is waiting for the trigger word, listening to
          their answer, thinking, etc. without having to guess from the
          message flow. */}
      <div
        style={{
          display: "flex",
          "align-items": "center",
          gap: "8px",
          "margin-bottom": "10px",
          "font-size": "11px",
          color: "var(--c-stone)",
          "letter-spacing": ".05em",
        }}
        class="f-mono"
      >
        <span
          style={{
            width: "8px",
            height: "8px",
            "border-radius": "50%",
            background: stateBadge(agentState()).color,
            "box-shadow": `0 0 6px ${stateBadge(agentState()).color}`,
            animation: stateBadge(agentState()).pulse ? "breathe 1.4s ease-in-out infinite" : "none",
          }}
        />
        <span style={{ color: stateBadge(agentState()).color }}>
          {stateBadge(agentState()).label}
        </span>
      </div>
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
                <span style={{ color: "var(--c-stone)" }}>
                  {new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
                <Show when={e.emotion}>
                  <span
                    style={{
                      display: "inline-flex",
                      "align-items": "center",
                      gap: "4px",
                      color: EMOTION_COLORS[e.emotion as Emotion] ?? "var(--c-mist)",
                    }}
                  >
                    <LafufuHead emotion={e.emotion as Emotion} size={16} />
                    {e.emotion}
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
        <Show when={activeStage()}>
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
              lafufu · {activeStage()!.name}… ⧗ {fmtElapsed(nowTick() - activeStage()!.since)}
            </span>
          </div>
        </Show>
        <Show when={entries().length === 0 && pendingSince() === null && stage() === null}>
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
                display: "inline-flex",
                "align-items": "center",
                gap: "8px",
              }}
            >
              <LafufuHead emotion={speakEmotion()} size={20} />
              speak it
            </button>
          </div>
        </div>
      </Show>
    </Panel>
  );
};
