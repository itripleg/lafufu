import { Component, createEffect, createSignal, For, Show } from "solid-js";
import type { NatsWs } from "../shared/nats_ws";
import { PetDevice } from "../pet/pet_device";
import { lsGet, lsSet } from "../shared/local_storage";
import { ChatLog } from "./chat_log";

/**
 * Companion — pet + chat together, with a layout switcher. Choice is
 * remembered per browser. Both share the admin's single NatsWs instance.
 */
type Layout = "even" | "side" | "stacked" | "pet" | "chat";

const LAYOUTS: { id: Layout; label: string; hint: string }[] = [
  { id: "even",    label: "= even",        hint: "Pet and chat at equal width" },
  { id: "side",    label: "⬛▭ side",       hint: "Pet left (square) · chat right" },
  { id: "stacked", label: "⬛/▭ stacked",   hint: "Pet on top · chat below" },
  { id: "pet",     label: "⬛ pet",         hint: "Pet full-bleed" },
  { id: "chat",    label: "▭ chat",        hint: "Chat full-bleed" },
];

// Rounded enclosure the pet sits in when embedded — provides its own backdrop.
const petShell = (extra: Record<string, string>) => ({
  position: "relative" as const,
  "border-radius": "20px",
  overflow: "hidden",
  border: "1px solid var(--c-edge)",
  background: "radial-gradient(circle at 50% 30%, #2d2018 0%, #1a1410 60%, #0c0907 100%)",
  "box-shadow": "inset 0 1px 0 rgba(255,240,210,.04)",
  ...extra,
});

export const Companion: Component<{ nats: NatsWs }> = (props) => {
  const [layout, setLayout] = createSignal<Layout>(lsGet<Layout>("companion/layout", "even"));
  createEffect(() => lsSet("companion/layout", layout()));

  return (
    <div style={{ display: "flex", "flex-direction": "column", gap: "14px" }}>
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

      {/* EVEN — 50/50 side by side; no outer border so PetDevice's own shell is the frame */}
      <Show when={layout() === "even"}>
        <div style={{ display: "flex", gap: "16px", "align-items": "stretch", "min-height": "62vh" }}>
          <div
            style={{
              flex: "1",
              "min-width": "0",
              position: "relative",
              overflow: "hidden",
              "border-radius": "20px",
              background:
                "radial-gradient(circle at 50% 30%, #2d2018 0%, #1a1410 60%, #0c0907 100%)",
            }}
          >
            <PetDevice nats={props.nats} />
          </div>
          <div style={{ flex: "1", "min-width": "0" }}>
            <ChatLog nats={props.nats} />
          </div>
        </div>
      </Show>

      {/* SIDE — square pet left · wider chat right */}
      <Show when={layout() === "side"}>
        <div style={{ display: "flex", gap: "16px", "align-items": "stretch" }}>
          <div style={petShell({ width: "min(42%, 460px)", "flex-shrink": "0", "min-height": "62vh" })}>
            <PetDevice nats={props.nats} />
          </div>
          <div style={{ flex: "1", "min-width": "0" }}>
            <ChatLog nats={props.nats} />
          </div>
        </div>
      </Show>

      {/* STACKED — pet on top, chat below */}
      <Show when={layout() === "stacked"}>
        <div style={{ display: "flex", "flex-direction": "column", gap: "16px" }}>
          <div style={petShell({ width: "100%", height: "44vh" })}>
            <PetDevice nats={props.nats} />
          </div>
          <ChatLog nats={props.nats} />
        </div>
      </Show>

      {/* PET — pet full-bleed */}
      <Show when={layout() === "pet"}>
        <div style={petShell({ width: "100%", height: "52vh" })}>
          <PetDevice nats={props.nats} />
        </div>
      </Show>

      {/* CHAT — chat full-bleed */}
      <Show when={layout() === "chat"}>
        <ChatLog nats={props.nats} />
      </Show>
    </div>
  );
};
