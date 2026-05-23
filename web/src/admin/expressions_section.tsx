import {
  Component,
  createMemo,
  createResource,
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

const PLAYBACK: ExpressionDTO["playback"][] = ["once", "loop", "shuffle"];
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

export const ExpressionsSection: Component = () => {
  const [expressions, { refetch: refetchExpr }] = createResource(async () =>
    (await api.listExpressions()).items,
  );
  const [frames] = createResource(async () => (await api.listFrames()).items);
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
      await refetchExpr();
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
        emotion: e.emotion,
        description: e.description,
      });
      await refetchExpr();
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
      await refetchExpr();
      setLocalEdits(null);
      setSelectedName(null);
    } catch (err: unknown) {
      toast.err("delete failed", (err as Error)?.message ?? String(err));
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
                <div class="font-mono">{e.name}</div>
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
                  <button
                    type="button"
                    onClick={onDelete}
                    class="px-3 py-1 border border-red-800 text-red-300 rounded hover:bg-red-900/30 ml-auto"
                  >
                    Delete
                  </button>
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
              </div>
          )}
        </Show>
      </div>
    </div>
  );
};
