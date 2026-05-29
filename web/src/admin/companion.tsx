import { Component, createEffect, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import type { NatsWs } from "../shared/nats_ws";
import { api, type ChatRow } from "../shared/api";
import { PetDevice } from "../pet/pet_device";
import { lsGet, lsSet } from "../shared/local_storage";
import { ChatLog, mergeHistory, type Entry } from "./chat_log";
import { toast } from "../shared/toast";

/**
 * Companion — pet + inline overlay chat. All non-"chat" layouts show the pet
 * with messages floating left (lafufu) and right (user), image centred. The
 * "chat" layout falls back to the full ChatLog panel with puppet mode.
 *
 * Layout controls the height of the overlay area only — the input always sits
 * directly below it and sticks to the viewport bottom on scroll.
 */
type Layout = "even" | "stacked" | "pet" | "chat";

const LAYOUTS: { id: Layout; label: string; hint: string }[] = [
  { id: "even",    label: "= even",    hint: "Overlay · mid height" },
  { id: "stacked", label: "⬛ tall",    hint: "Overlay · tall · inline chat" },
  { id: "pet",     label: "⬛ pet",     hint: "Overlay · compact" },
  { id: "chat",    label: "▭ chat",    hint: "Full chat panel" },
];

// Height of the pet+overlay area per layout — sized to keep the input in view.
const PET_H: Record<Layout, string> = {
  even:    "58vh",
  stacked: "64vh",
  pet:     "46vh",
  chat:    "0",
};

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

const Bubble = (props: { text: string; side: "left" | "right"; label: string }) => (
  <div
    style={{
      "pointer-events": "auto",
      background: props.side === "left" ? "rgba(38,55,32,.88)" : "rgba(50,42,28,.88)",
      "backdrop-filter": "blur(10px)",
      "-webkit-backdrop-filter": "blur(10px)",
      border: "1px solid rgba(255,240,210,.13)",
      "border-radius": props.side === "left" ? "10px 10px 10px 3px" : "10px 10px 3px 10px",
      padding: "6px 9px",
      "max-width": "100%",
      "word-break": "break-word",
      color: "var(--c-bone)",
      "font-size": "11.5px",
      "line-height": "1.45",
      animation: "fade-up .25s cubic-bezier(.2,.7,.3,1.1) both",
    }}
  >
    <div
      style={{
        "font-size": "9px",
        "font-family": "var(--f-mono)",
        color: props.side === "left" ? "var(--c-moss)" : "var(--c-mist)",
        "margin-bottom": "3px",
        "letter-spacing": ".04em",
      }}
    >
      {props.label}
    </div>
    {props.text}
  </div>
);

export const Companion: Component<{ nats: NatsWs }> = (props) => {
  const [layout, setLayout] = createSignal<Layout>(lsGet<Layout>("companion/layout", "even"));
  createEffect(() => lsSet("companion/layout", layout()));

  // Lightweight chat state — full history only in "chat" layout (ChatLog).
  // Overlay shows last 5 per side (it's ambient context, not a log).
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [chatInput, setChatInput] = createSignal("");
  const [sending, setSending] = createSignal(false);

  const appendEntry = (entry: Omit<Entry, "ts">) => {
    setEntries((prev) => {
      const last = prev[prev.length - 1];
      const now = Date.now();
      if (last && last.role === entry.role && last.text === entry.text && now - last.ts < 500)
        return prev;
      return [...prev.slice(-99), { ...entry, ts: now }];
    });
  };

  onMount(() => {
    const subs: Array<() => void> = [
      props.nats.subscribe("agent.transcript", (f) =>
        appendEntry({ role: "user", text: f.payload.text }),
      ),
      props.nats.subscribe("agent.reply", (f) => {
        const role: Entry["role"] = f.payload.source === "puppet" ? "puppet" : "lafufu";
        appendEntry({ role, text: f.payload.text, emotion: f.payload.emotion });
      }),
    ];
    onCleanup(() => subs.forEach((u) => u()));

    api
      .chatMessages()
      .then(({ messages }) => {
        const history = messages.map(rowToEntry);
        setEntries((live) => mergeHistory(history, live));
      })
      .catch(() => {});
  });

  const sendChat = async () => {
    const text = chatInput().trim();
    if (!text || sending()) return;
    setSending(true);
    setChatInput("");
    try {
      await api.agentTextMessage(text);
    } catch (err: any) {
      toast.err("chat failed", err.message);
      setChatInput(text);
    } finally {
      setSending(false);
    }
  };

  const lafufuMsgs = () =>
    entries()
      .filter((e) => e.role === "lafufu" || e.role === "puppet")
      .slice(-5);
  const userMsgs = () => entries().filter((e) => e.role === "user").slice(-5);

  return (
    <div style={{ display: "flex", "flex-direction": "column", gap: "8px" }}>
      {/* Layout switcher */}
      <div
        role="tablist"
        aria-label="companion layout"
        style={{
          display: "flex",
          gap: "4px",
          padding: "4px",
          background: "var(--c-shell)",
          border: "1px solid var(--c-edge)",
          "border-radius": "12px",
          "align-self": "flex-start",
        }}
      >
        <For each={LAYOUTS}>
          {(l) => (
            <button
              type="button"
              class={layout() === l.id ? "btn btn--primary btn--micro" : "btn btn--micro"}
              style={{ border: "none" }}
              title={l.hint}
              onClick={() => setLayout(l.id)}
            >
              {l.label}
            </button>
          )}
        </For>
      </div>

      {/* ── OVERLAY LAYOUTS ─────────────────────────────────────────────── */}
      <Show when={layout() !== "chat"}>
        {/* Pet + message columns */}
        <div
          style={{
            position: "relative",
            height: PET_H[layout()],
            "border-radius": "20px",
            overflow: "hidden",
            background:
              "radial-gradient(circle at 50% 30%, #2d2018 0%, #1a1410 60%, #0c0907 100%)",
          }}
        >
          {/* PetDevice fills the container */}
          <div style={{ position: "absolute", inset: "0" }}>
            <PetDevice nats={props.nats} />
          </div>

          {/* Message overlay — pointer-events:none so pet drag works in the centre */}
          <div
            style={{
              position: "absolute",
              inset: "0",
              display: "flex",
              "pointer-events": "none",
            }}
          >
            {/* Left column — lafufu messages */}
            <div
              style={{
                width: "30%",
                display: "flex",
                "flex-direction": "column",
                "justify-content": "flex-end",
                gap: "6px",
                padding: "14px 6px 16px 12px",
                overflow: "hidden",
              }}
            >
              <For each={lafufuMsgs()}>
                {(e) => (
                  <Bubble
                    text={e.text}
                    side="left"
                    label={e.emotion ? `lafufu · ${e.emotion}` : "lafufu"}
                  />
                )}
              </For>
            </div>

            {/* Centre — transparent, pet drag passthrough */}
            <div style={{ flex: 1 }} />

            {/* Right column — user messages */}
            <div
              style={{
                width: "30%",
                display: "flex",
                "flex-direction": "column",
                "justify-content": "flex-end",
                "align-items": "flex-end",
                gap: "6px",
                padding: "14px 12px 16px 6px",
                overflow: "hidden",
              }}
            >
              <For each={userMsgs()}>
                {(e) => <Bubble text={e.text} side="right" label="you" />}
              </For>
            </div>
          </div>
        </div>

        {/* Input — below the pet area, sticky to viewport bottom */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            position: "sticky",
            bottom: "12px",
          }}
        >
          <input
            class="field"
            style={{ flex: 1, "font-family": "var(--f-sans)" }}
            placeholder="ask lafufu something…"
            value={chatInput()}
            disabled={sending()}
            onInput={(e) => setChatInput(e.currentTarget.value)}
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

      {/* ── CHAT LAYOUT — full ChatLog with puppet mode ──────────────────── */}
      <Show when={layout() === "chat"}>
        <ChatLog nats={props.nats} />
      </Show>
    </div>
  );
};
