import { Component, Show } from "solid-js";

interface Props {
  text: () => string | undefined;
  tint?: () => string;
}

export const Caption: Component<Props> = (props) => {
  return (
    <div
      style={{
        position: "absolute",
        bottom: "max(64px, env(safe-area-inset-bottom))",
        left: "8vw", right: "8vw",
        display: "flex",
        "justify-content": "center",
        "pointer-events": "none",
      }}
    >
      <Show when={props.text()}>
        <div
          style={{
            "max-width": "min(900px, 84vw)",
            "padding": "20px 28px",
            "border-radius": "22px",
            "background": "rgba(15, 11, 8, 0.55)",
            "backdrop-filter": "blur(18px) saturate(120%)",
            "-webkit-backdrop-filter": "blur(18px) saturate(120%)",
            "border": "1px solid rgba(243, 236, 220, 0.08)",
            "box-shadow":
              "0 1px 0 rgba(255,240,210,.04) inset, 0 24px 60px -24px rgba(0,0,0,.7)",
            "text-align": "center",
            animation: "fade-up .55s cubic-bezier(.2,.7,.3,1.1) both",
          }}
        >
          <div
            class="eyebrow"
            style={{
              color: props.tint ? props.tint() : "var(--c-mist)",
              "margin-bottom": "8px",
              "justify-content": "center",
              display: "flex",
            }}
          >
            transcript
          </div>
          <div
            class="f-display"
            style={{
              "font-size": "clamp(22px, 3.4vw, 44px)",
              "line-height": 1.18,
              color: "var(--c-bone)",
              "font-style": "italic",
            }}
          >
            {props.text()}
          </div>
        </div>
      </Show>
    </div>
  );
};
