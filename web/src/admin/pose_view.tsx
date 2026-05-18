import { Component, createSignal, onCleanup, onMount, For } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { Panel } from "./panel";

const SERVOS: Array<{ name: string; range: [number, number]; glyph: string }> = [
  { name: "head_lr", range: [1828, 2298], glyph: "↔" },
  { name: "head_ud", range: [2885, 3278], glyph: "↕" },
  { name: "eye",     range: [1960, 2130], glyph: "◉" },
  { name: "jaw",     range: [1534, 1728], glyph: "▽" },
  { name: "brow",    range: [2051, 2099], glyph: "︿" },
];

const fmt = (n: number | undefined) => n === undefined ? "—" : n.toFixed(0);

export const PoseView: Component<{ nats: NatsWs }> = (props) => {
  const [pose, setPose] = createSignal<Record<string, number>>({});

  let unsub: (() => void) | undefined;
  onMount(() => { unsub = props.nats.subscribe("animator.pose", (f) => setPose(f.payload)); });
  onCleanup(() => unsub?.());

  return (
    <Panel
      title="Live pose"
      eyebrow="animator.pose · 50Hz"
      accent="var(--c-iris)"
    >
      <div
        style={{
          display: "grid",
          "grid-template-columns": "repeat(auto-fit, minmax(100px, 1fr))",
          gap: "10px",
        }}
      >
        <For each={SERVOS}>
          {(servo) => {
            const v = () => pose()[servo.name];
            const frac = () => {
              const x = v();
              if (x === undefined) return 0;
              const [lo, hi] = servo.range;
              return Math.max(0, Math.min(1, (x - lo) / (hi - lo)));
            };
            return (
              <div
                style={{
                  padding: "12px 10px",
                  "border-radius": "14px",
                  background: "var(--c-shell)",
                  border: "1px solid var(--c-edge)",
                  position: "relative",
                  overflow: "hidden",
                  "text-align": "center",
                }}
              >
                {/* Fill bar — fraction within range */}
                <div
                  style={{
                    position: "absolute",
                    bottom: 0, left: 0,
                    width: "100%",
                    height: `${frac() * 100}%`,
                    background:
                      "linear-gradient(180deg, transparent, rgba(149,176,122,0.18))",
                    transition: "height var(--t-base)",
                    "pointer-events": "none",
                  }}
                />
                <div
                  style={{ position: "relative", "z-index": 1 }}
                >
                  <div
                    class="f-mono"
                    style={{
                      "font-size": "9px",
                      color: "var(--c-stone)",
                      "letter-spacing": ".15em",
                      "text-transform": "uppercase",
                      "margin-bottom": "4px",
                    }}
                  >
                    <span style={{ color: "var(--c-mist)", "margin-right": "4px" }}>{servo.glyph}</span>
                    {servo.name}
                  </div>
                  <div
                    class="f-num f-mono"
                    style={{
                      "font-size": "22px",
                      color: v() === undefined ? "var(--c-stone)" : "var(--c-bone)",
                      "line-height": 1.1,
                    }}
                  >
                    {fmt(v())}
                  </div>
                  <div
                    class="f-mono f-num"
                    style={{
                      "font-size": "9px",
                      color: "var(--c-stone)",
                      "margin-top": "2px",
                    }}
                  >
                    {servo.range[0]}–{servo.range[1]}
                  </div>
                </div>
              </div>
            );
          }}
        </For>
      </div>
    </Panel>
  );
};
