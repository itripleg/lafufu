import { Component, createSignal, For } from "solid-js";
import { api } from "../shared/api";
import { EMOTION_COLORS, EMOTION_GLYPH, type Emotion } from "../shared/design";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

const EXPRESSIONS: Emotion[] = ["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"];

export const ExpressionButtons: Component = () => {
  const [active, setActive] = createSignal<string | null>(null);

  const trigger = async (name: Emotion) => {
    setActive(name);
    try {
      await api.animatorExpression(name);
      toast.ok(`expression: ${name}`);
    } catch (e: any) {
      toast.err(`expression failed`, e.message);
    } finally {
      window.setTimeout(() => setActive((cur) => (cur === name ? null : cur)), 600);
    }
  };

  return (
    <Panel
      title="Expressions"
      eyebrow="trigger · /api/animator/expression"
      accent="var(--c-petal)"
    >
      <div
        style={{
          display: "grid",
          "grid-template-columns": "repeat(auto-fit, minmax(120px, 1fr))",
          gap: "10px",
        }}
      >
        <For each={EXPRESSIONS}>
          {(name) => {
            const color = EMOTION_COLORS[name];
            const isActive = () => active() === name;
            return (
              <button
                onClick={() => trigger(name)}
                style={{
                  display: "flex",
                  "flex-direction": "column",
                  "align-items": "flex-start",
                  gap: "6px",
                  padding: "14px 14px",
                  "border-radius": "14px",
                  background: isActive()
                    ? `linear-gradient(155deg, ${color}33, ${color}11)`
                    : "rgba(243, 236, 220, 0.03)",
                  border: `1px solid ${isActive() ? color : "var(--c-edge)"}`,
                  color: "var(--c-bone)",
                  cursor: "pointer",
                  transition: "transform var(--t-fast), background var(--t-fast), border-color var(--t-fast)",
                  "text-align": "left",
                  position: "relative",
                  overflow: "hidden",
                }}
                onMouseOver={(e) => { e.currentTarget.style.transform = "translateY(-1px)"; }}
                onMouseOut={(e)  => { e.currentTarget.style.transform = "translateY(0)"; }}
              >
                <span
                  class="f-mono"
                  style={{
                    "font-size": "18px",
                    color,
                    "text-shadow": isActive() ? `0 0 12px ${color}` : "none",
                    transition: "text-shadow var(--t-fast)",
                  }}
                >
                  {EMOTION_GLYPH[name]}
                </span>
                <span
                  class="f-display-roman"
                  style={{ "font-size": "15px", color: "var(--c-bone)" }}
                >
                  {name}
                </span>
              </button>
            );
          }}
        </For>
      </div>
    </Panel>
  );
};
