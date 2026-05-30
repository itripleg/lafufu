import { Component, createSignal, onCleanup, onMount, For, Show, createMemo } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { lsGet, lsSet } from "../shared/local_storage";
import { Panel } from "./panel";
import { useLayoutMode } from "../shared/use_media";

interface Line { ts: number; topic: string; payload: any; }

const FILTER_KEY = "pulse/filter";
const PAUSED_KEY = "pulse/paused";
const TELEMETRY_KEY = "pulse/telemetry";

// High-frequency telemetry (~20 Hz) that floods the firehose and drowns out
// meaningful events. Dropped at ingest unless the "telemetry" toggle is on, so
// the 200-line buffer stays full of signal rather than pose/rms spam.
const NOISY = new Set(["animator.pose", "agent.tts.rms"]);

const topicColor = (topic: string): string => {
  if (topic.startsWith("agent."))    return "var(--c-moss)";
  if (topic.startsWith("animator.")) return "var(--c-iris)";
  if (topic.startsWith("printer."))  return "var(--c-petal)";
  if (topic.startsWith("system."))   return "var(--c-amber)";
  if (topic.startsWith("config."))   return "var(--c-mauve)";
  return "var(--c-mist)";
};

export const SystemPulse: Component<{ nats: NatsWs }> = (props) => {
  const layout = useLayoutMode();
  // The firehose is a fixed 320px window on desktop; on a tall portrait screen
  // let it stretch so the pulse uses the spare vertical room.
  const feedMaxHeight = () => (layout() === "long" ? "62vh" : "320px");
  const [lines, setLines] = createSignal<Line[]>([]);
  const [filter, setFilter] = createSignal(lsGet<string>(FILTER_KEY, ""));
  const [paused, setPaused] = createSignal(lsGet<boolean>(PAUSED_KEY, false));
  const [showTelemetry, setShowTelemetry] = createSignal(lsGet<boolean>(TELEMETRY_KEY, false));

  let unsub: (() => void) | undefined;
  onMount(() => {
    unsub = props.nats.subscribe(">", (f) => {
      if (paused()) return;
      if (!showTelemetry() && NOISY.has(f.topic)) return;
      setLines((ls) => [...ls.slice(-199), { ts: Date.now(), topic: f.topic, payload: f.payload }]);
    });
  });
  onCleanup(() => unsub?.());

  const visible = createMemo(() => {
    const q = filter().trim().toLowerCase();
    if (!q) return lines();
    return lines().filter((l) =>
      l.topic.toLowerCase().includes(q) ||
      JSON.stringify(l.payload).toLowerCase().includes(q),
    );
  });

  const onFilter = (v: string) => { setFilter(v); lsSet(FILTER_KEY, v); };
  const togglePause = () => { const p = !paused(); setPaused(p); lsSet(PAUSED_KEY, p); };
  const toggleTelemetry = () => { const v = !showTelemetry(); setShowTelemetry(v); lsSet(TELEMETRY_KEY, v); };
  const clear = () => setLines([]);

  return (
    <Panel
      title="System pulse"
      eyebrow="nats firehose · subscribe '>'"
      accent="var(--c-mauve)"
      actions={
        <>
          <button
            class={`btn btn--tiny ${showTelemetry() ? "" : "btn--ghost"}`}
            onClick={toggleTelemetry}
            title="Include high-frequency telemetry (animator.pose, agent.tts.rms)"
          >
            {showTelemetry() ? "telemetry on" : "telemetry off"}
          </button>
          <button
            class={`btn btn--tiny ${paused() ? "" : "btn--ghost"}`}
            classList={{ "btn--coral": paused() }}
            onClick={togglePause}
          >
            {paused() ? "▶ resume" : "❚❚ pause"}
          </button>
          <button class="btn btn--ghost btn--tiny" onClick={clear}>
            clear
          </button>
        </>
      }
    >
      <input
        type="text"
        class="field"
        style={{ width: "100%", "margin-bottom": "12px" }}
        placeholder="filter by topic or payload…"
        value={filter()}
        onInput={(e) => onFilter(e.currentTarget.value)}
      />
      <div
        class="f-mono scroll-warm"
        style={{
          "font-size": "11px",
          "max-height": feedMaxHeight(),
          "overflow-y": "auto",
          background: "var(--c-shell)",
          "border-radius": "12px",
          border: "1px solid var(--c-edge)",
          padding: "10px 12px",
          display: "flex",
          "flex-direction": "column",
          gap: "2px",
        }}
      >
        <For each={visible()}>
          {(l) => (
            <div
              style={{
                display: "grid",
                "grid-template-columns": "68px minmax(0, 1.4fr) minmax(0, 2fr)",
                gap: "10px",
                "padding": "2px 0",
              }}
            >
              <span style={{ color: "var(--c-stone)", "white-space": "nowrap" }}>
                {new Date(l.ts).toLocaleTimeString()}
              </span>
              <span
                style={{
                  color: topicColor(l.topic),
                  "white-space": "nowrap",
                  overflow: "hidden",
                  "text-overflow": "ellipsis",
                }}
                title={l.topic}
              >
                {l.topic}
              </span>
              <span
                style={{
                  color: "var(--c-mist)",
                  overflow: "hidden",
                  "text-overflow": "ellipsis",
                  "white-space": "nowrap",
                }}
                title={JSON.stringify(l.payload)}
              >
                {JSON.stringify(l.payload)}
              </span>
            </div>
          )}
        </For>
        <Show when={visible().length === 0}>
          <div style={{ color: "var(--c-stone)", "text-align": "center", padding: "20px 0" }}>
            {paused() ? "(paused — resume to see events)" : "no events yet"}
          </div>
        </Show>
      </div>
    </Panel>
  );
};
