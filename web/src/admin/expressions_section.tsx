import {
  Component,
  createMemo,
  createSignal,
  For,
  Show,
} from "solid-js";
import {
  DragDropProvider,
  DragDropSensors,
  SortableProvider,
  createSortable,
  type Id,
} from "@thisbeyond/solid-dnd";
import {
  api,
  type ExpressionDTO,
  type ExpressionStepDTO,
  type FrameDTO,
} from "../shared/api";
import { toast } from "../shared/toast";
import type { NatsWs } from "../shared/nats_ws";
import { createReactiveResource } from "../shared/reactive_resource";

const SortableStep: Component<{
  id: string;
  label: string;
  onRemove: () => void;
}> = (props) => {
  const sortable = createSortable(props.id);
  return (
    <div
      ref={sortable.ref}
      {...sortable.dragActivators}
      class="flex items-center gap-1 px-2 py-1 bg-stone-800 rounded text-sm cursor-grab active:cursor-grabbing select-none touch-none"
    >
      <span class="font-mono">{props.label}</span>
      <button
        type="button"
        class="text-red-400 hover:text-red-200"
        onClick={(ev) => {
          ev.stopPropagation();
          props.onRemove();
        }}
        aria-label="remove"
      >
        ×
      </button>
    </div>
  );
};

const PLAYBACK: ExpressionDTO["playback"][] = ["once", "loop", "shuffle", "random_walk"];
const DEFAULT_RW = { intensity: 1.0, speed: 1.0, pause_chance: 0.30 };
const EMOTIONS = [
  "idle",
  "agree",
  "disagree",
  "happy",
  "sad",
  "angry",
  "surprised",
  "neutral",
] as const;

export const ExpressionsSection: Component<{ nats: NatsWs }> = (props) => {
  const expressions = createReactiveResource(
    async () => (await api.listExpressions()).items,
    ["expressions.changed"],
    props.nats,
  );
  const frames = createReactiveResource(
    async () => (await api.listFrames()).items,
    ["frames.changed"],
    props.nats,
  );
  const [selectedName, setSelectedName] = createSignal<string | null>(null);

  // Local override layer: while a fresh resource fetch is in-flight or
  // before save, the user's in-progress edits live here. After successful
  // refetch the layer clears.
  const [localEdits, setLocalEdits] = createSignal<ExpressionDTO[] | null>(null);
  const effective = createMemo<ExpressionDTO[] | null>(
    () => localEdits() ?? expressions() ?? null,
  );

  // Override `selected` memo to read from effective list.
  const selectedEff = createMemo<ExpressionDTO | null>(
    () => effective()?.find((e) => e.name === selectedName()) ?? null,
  );

  const mutateSelected = (patch: Partial<ExpressionDTO>) => {
    const list = effective();
    if (!list) return;
    const i = list.findIndex((e) => e.name === selectedName());
    if (i < 0) return;
    const next = [...list];
    next[i] = { ...next[i], ...patch };
    setLocalEdits(next);
  };

  const onNew = async () => {
    const name = window.prompt("expression name:");
    if (!name) return;
    try {
      await api.createExpression({ name: name.trim(), steps: [] });
      setLocalEdits(null);
      setSelectedName(name.trim());
    } catch (e: unknown) {
      toast.err("create failed", (e as Error)?.message ?? String(e));
    }
  };

  const onSave = async () => {
    const e = selectedEff();
    if (!e) return;
    try {
      await api.updateExpression(e.name, {
        playback: e.playback,
        default_duration_ms: e.default_duration_ms,
        default_delay_ms: e.default_delay_ms,
        default_easing: e.default_easing,
        steps: e.steps,
        random_walk_config: e.random_walk_config,
        emotion: e.emotion,
        description: e.description,
      });
      setLocalEdits(null);
      toast.ok(`saved ${e.name}`);
    } catch (err: unknown) {
      toast.err("save failed", (err as Error)?.message ?? String(err));
    }
  };

  const onPlay = async () => {
    const e = selectedEff();
    if (!e) return;
    try {
      await api.playExpression(e.name);
    } catch (err: unknown) {
      toast.err("play failed", (err as Error)?.message ?? String(err));
    }
  };

  const onDelete = async () => {
    const e = selectedEff();
    if (!e) return;
    if (!window.confirm(`Delete expression "${e.name}"?`)) return;
    try {
      await api.deleteExpression(e.name);
      setLocalEdits(null);
      setSelectedName(null);
    } catch (err: unknown) {
      toast.err("delete failed", (err as Error)?.message ?? String(err));
    }
  };

  const onReset = async () => {
    const e = selectedEff();
    if (!e) return;
    if (!window.confirm(`Reset "${e.name}" to factory defaults? Your edits will be lost.`)) return;
    try {
      await api.resetExpression(e.name);
      // expressions.changed publish will refetch automatically; clear local edits
      setLocalEdits(null);
      toast.ok(`reset ${e.name}`);
    } catch (err: unknown) {
      toast.err("reset failed", (err as Error)?.message ?? String(err));
    }
  };

  const addStep = (frameName: string) => {
    const e = selectedEff();
    if (!e) return;
    const step: ExpressionStepDTO = { frame: frameName };
    mutateSelected({ steps: [...e.steps, step] });
  };

  const removeStep = (idx: number) => {
    const e = selectedEff();
    if (!e) return;
    const next = e.steps.slice();
    next.splice(idx, 1);
    mutateSelected({ steps: next });
  };

  return (
    <div class="border border-stone-700 rounded-lg p-4 bg-stone-900/40">
      <div class="flex items-baseline justify-between mb-3">
        <h2 class="text-lg font-semibold">Expressions</h2>
        <button
          type="button"
          onClick={onNew}
          class="px-3 py-1 border border-amber-600 text-amber-300 rounded hover:bg-amber-900/30"
        >
          + New expression
        </button>
      </div>

      <div class="grid gap-4 grid-cols-[1fr_2fr]">
        {/* ── left: list ── */}
        <div class="border border-stone-700 rounded p-2 max-h-96 overflow-y-auto">
          <For each={effective() ?? []}>
            {(e) => (
              <button
                type="button"
                onClick={() => setSelectedName(e.name)}
                class={`block w-full text-left px-2 py-1 rounded text-sm hover:bg-stone-800 ${
                  selectedName() === e.name ? "bg-amber-500/20 text-amber-200" : ""
                }`}
              >
                <div class="font-mono">
                  {e.name}
                  <Show when={e.is_builtin}>
                    <span class="ml-2 text-xs text-stone-500">(builtin)</span>
                  </Show>
                </div>
                <div class="text-xs text-stone-400">
                  {e.playback} · {e.steps.length} step(s)
                  {e.emotion ? ` · ${e.emotion}` : ""}
                </div>
              </button>
            )}
          </For>
        </div>

        {/* ── right: editor ── */}
        <Show
          when={selectedEff()}
          fallback={
            <div class="text-stone-500 italic p-4">
              Select an expression, or create a new one.
            </div>
          }
        >
          {(e) => (
              <div class="flex flex-col gap-3">
                <div class="font-mono text-amber-300 text-lg">{e().name}</div>

                <div class="flex gap-2 flex-wrap">
                  <button
                    type="button"
                    onClick={onPlay}
                    class="px-3 py-1 border border-green-700 text-green-300 rounded hover:bg-green-900/30"
                  >
                    ▶ Play
                  </button>
                  <button
                    type="button"
                    onClick={onSave}
                    class="px-3 py-1 border border-amber-600 text-amber-300 rounded hover:bg-amber-900/30"
                  >
                    Save
                  </button>
                  <Show when={e().is_builtin}>
                    <button
                      type="button"
                      onClick={onReset}
                      class="px-3 py-1 border border-blue-700 text-blue-300 rounded hover:bg-blue-900/30 ml-auto"
                    >
                      Reset to defaults
                    </button>
                  </Show>
                  <Show when={!e().is_builtin}>
                    <button
                      type="button"
                      onClick={onDelete}
                      class="px-3 py-1 border border-red-800 text-red-300 rounded hover:bg-red-900/30 ml-auto"
                    >
                      Delete
                    </button>
                  </Show>
                </div>

                <div class="flex gap-3 items-center text-sm">
                  <label class="flex items-center gap-1">
                    Playback
                    <select
                      value={e().playback}
                      onChange={(ev) =>
                        mutateSelected({
                          playback: ev.currentTarget
                            .value as ExpressionDTO["playback"],
                        })
                      }
                      class="bg-stone-800 border border-stone-600 rounded px-1 py-0.5"
                    >
                      <For each={PLAYBACK}>{(p) => <option value={p}>{p}</option>}</For>
                    </select>
                  </label>
                  <label class="flex items-center gap-1">
                    Emotion
                    <select
                      value={e().emotion ?? ""}
                      onChange={(ev) => {
                        const v = ev.currentTarget.value;
                        mutateSelected({ emotion: v === "" ? null : v });
                      }}
                      class="bg-stone-800 border border-stone-600 rounded px-1 py-0.5"
                    >
                      <option value="">(none)</option>
                      <For each={EMOTIONS}>{(m) => <option value={m}>{m}</option>}</For>
                    </select>
                  </label>
                </div>

                <Show when={e().playback === "random_walk"}>
                  {(() => {
                    const cfg = () => e().random_walk_config ?? DEFAULT_RW;
                    const set = (patch: Partial<typeof DEFAULT_RW>) =>
                      mutateSelected({ random_walk_config: { ...cfg(), ...patch } });
                    return (
                      <div class="border border-stone-700 rounded p-3 flex flex-col gap-2">
                        <div class="text-xs uppercase tracking-wide text-stone-400">
                          Random walk
                        </div>
                        <label class="flex items-center gap-2 text-sm">
                          <span class="w-28 text-stone-400">intensity</span>
                          <input
                            type="range"
                            min={0} max={2} step={0.05}
                            value={cfg().intensity}
                            onInput={(ev) => set({ intensity: Number(ev.currentTarget.value) })}
                            class="flex-1"
                          />
                          <span class="font-mono w-12 text-right">{cfg().intensity.toFixed(2)}</span>
                        </label>
                        <label class="flex items-center gap-2 text-sm">
                          <span class="w-28 text-stone-400">speed</span>
                          <input
                            type="range"
                            min={0.1} max={4} step={0.05}
                            value={cfg().speed}
                            onInput={(ev) => set({ speed: Number(ev.currentTarget.value) })}
                            class="flex-1"
                          />
                          <span class="font-mono w-12 text-right">{cfg().speed.toFixed(2)}</span>
                        </label>
                        <label class="flex items-center gap-2 text-sm">
                          <span class="w-28 text-stone-400">pause chance</span>
                          <input
                            type="range"
                            min={0} max={1} step={0.05}
                            value={cfg().pause_chance}
                            onInput={(ev) => set({ pause_chance: Number(ev.currentTarget.value) })}
                            class="flex-1"
                          />
                          <span class="font-mono w-12 text-right">{cfg().pause_chance.toFixed(2)}</span>
                        </label>
                        <div class="text-xs text-stone-500">
                          intensity scales amplitude · speed shortens segments · pause-chance is fraction of held segments
                        </div>
                      </div>
                    );
                  })()}
                </Show>

                <Show when={e().playback !== "random_walk"}>
                <div>
                  <div class="text-xs uppercase tracking-wide text-stone-400 mb-1">
                    Steps
                  </div>
                  <DragDropProvider
                    onDragEnd={({ draggable, droppable }) => {
                      if (!draggable || !droppable) return;
                      const from = parseInt(
                        String(draggable.id).split("-")[1],
                        10,
                      );
                      const to = parseInt(
                        String(droppable.id).split("-")[1],
                        10,
                      );
                      if (
                        Number.isNaN(from) ||
                        Number.isNaN(to) ||
                        from === to
                      )
                        return;
                      const expr = selectedEff();
                      if (!expr) return;
                      const next = expr.steps.slice();
                      const [moved] = next.splice(from, 1);
                      next.splice(to, 0, moved);
                      mutateSelected({ steps: next });
                    }}
                  >
                    <DragDropSensors />
                    <SortableProvider
                      ids={e().steps.map((_, i) => `step-${i}`) as Id[]}
                    >
                      <div class="flex flex-wrap gap-1">
                        <For
                          each={e().steps}
                          fallback={
                            <span class="text-xs text-stone-500 italic">
                              no steps — add a frame below
                            </span>
                          }
                        >
                          {(step, i) => (
                            <SortableStep
                              id={`step-${i()}`}
                              label={step.frame}
                              onRemove={() => removeStep(i())}
                            />
                          )}
                        </For>
                      </div>
                    </SortableProvider>
                  </DragDropProvider>
                </div>

                <div>
                  <div class="text-xs uppercase tracking-wide text-stone-400 mb-1">
                    Add frame
                  </div>
                  <div class="flex flex-wrap gap-1">
                    <For each={frames() ?? []}>
                      {(f: FrameDTO) => (
                        <button
                          type="button"
                          onClick={() => addStep(f.name)}
                          class="px-2 py-0.5 border border-stone-600 rounded text-xs hover:bg-stone-800"
                        >
                          {f.name}
                        </button>
                      )}
                    </For>
                  </div>
                </div>
                </Show>
              </div>
          )}
        </Show>
      </div>
    </div>
  );
};
