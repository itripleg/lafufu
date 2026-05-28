import { Component, createSignal, For, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { lsGet, lsSet } from "../shared/local_storage";
import { ExpressionsSection } from "./expressions_section";
import { FramesSection } from "./frames_section";

type HeadView = "frames" | "expressions";

/**
 * Head tab — low-level animation authoring (frames + expressions).
 *
 * Secondary to the Studio (which has the richer drag-and-drop UX), so instead
 * of stacking both full sections it keeps a single compact view behind a
 * Frames/Expressions sub-toggle. No features are removed — each view is the
 * complete editor; only the layout is slimmed.
 */
export const HeadPanel: Component<{ nats: NatsWs }> = (props) => {
  const [view, setView] = createSignal<HeadView>(
    lsGet<HeadView>("admin/head-view", "frames"),
  );
  const select = (v: HeadView) => {
    setView(v);
    lsSet("admin/head-view", v);
  };

  return (
    <div class="flex flex-col gap-3">
      <div class="flex items-center gap-3">
        <div class="flex rounded border border-stone-600 overflow-hidden text-sm">
          <For each={["frames", "expressions"] as const}>
            {(v) => (
              <button
                type="button"
                onClick={() => select(v)}
                class={`px-3 py-1 ${v === "expressions" ? "border-l border-stone-600" : ""} ${
                  view() === v
                    ? "bg-amber-500/20 text-amber-200"
                    : "text-stone-400 hover:bg-stone-800"
                }`}
              >
                {v}
              </button>
            )}
          </For>
        </div>
        <span class="text-xs text-stone-500">
          low-level authoring · the Studio tab has the richer drag-and-drop editor
        </span>
      </div>

      <Show when={view() === "frames"}>
        <FramesSection nats={props.nats} />
      </Show>
      <Show when={view() === "expressions"}>
        <ExpressionsSection nats={props.nats} />
      </Show>
    </div>
  );
};
