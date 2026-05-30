import { Component, JSX, Show } from "solid-js";

interface Props {
  title: string;
  eyebrow?: string;
  accent?: string;
  actions?: JSX.Element;
  children?: JSX.Element;
  style?: JSX.CSSProperties;
  scroll?: boolean;
  /** When set, panel inner content uses fixed height with scroll-warm overflow. */
  height?: string;
  /** Make the inner body a flex column that fills remaining vertical space.
   *  Use when the consumer has a fixed-height panel (e.g. chat) and wants
   *  its content (scroll area + input row) to share the leftover height. */
  fullHeight?: boolean;
}

/** Shared biomorphic panel chrome — used by every admin section. */
export const Panel: Component<Props> = (props) => {
  return (
    <section
      class="pebble"
      style={{
        padding: "20px 22px",
        position: "relative",
        overflow: "hidden",
        ...(props.style ?? {}),
      }}
    >
      {/* Soft accent slash on the top-left — subtle "premium" cue */}
      <Show when={props.accent}>
        <div
          style={{
            position: "absolute",
            top: 0, left: 0,
            width: "60px", height: "60px",
            background: `radial-gradient(circle at top left, ${props.accent} 0%, transparent 70%)`,
            opacity: .25,
            "pointer-events": "none",
          }}
        />
      </Show>

      <header
        style={{
          display: "flex",
          "align-items": "flex-start",
          "justify-content": "space-between",
          /* Wrap so the title block and the action buttons drop onto separate
             rows on narrow viewports instead of overlapping. */
          "flex-wrap": "wrap",
          gap: "12px",
          "margin-bottom": "16px",
        }}
      >
        <div style={{ "min-width": 0, flex: "1 1 auto" }}>
          <Show when={props.eyebrow}>
            <div class="eyebrow" style={{ "margin-bottom": "6px", color: props.accent ?? "var(--c-stone)" }}>
              {props.eyebrow}
            </div>
          </Show>
          <h2
            class="f-display-roman"
            style={{
              margin: 0,
              "font-size": "22px",
              color: "var(--c-bone)",
            }}
          >
            {props.title}
          </h2>
        </div>
        <Show when={props.actions}>
          <div style={{ display: "flex", gap: "8px", "align-items": "center", "flex-wrap": "wrap", "flex-shrink": 0 }}>
            {props.actions}
          </div>
        </Show>
      </header>

      <div
        class={props.scroll || props.height ? "scroll-warm" : undefined}
        style={{
          "overflow-y": props.scroll || props.height ? "auto" : undefined,
          "max-height": props.height,
          "padding-right": props.scroll || props.height ? "6px" : undefined,
          /* fullHeight: make the body a flex column that takes the leftover
             vertical space in a fixed-height panel. Required for the chat's
             scroll-area-plus-pinned-input layout to actually fill the panel. */
          ...(props.fullHeight
            ? {
                flex: "1 1 0",
                "min-height": "0",
                display: "flex",
                "flex-direction": "column",
              }
            : {}),
        }}
      >
        {props.children}
      </div>
    </section>
  );
};
