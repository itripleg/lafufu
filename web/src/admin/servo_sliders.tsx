import { Component, createSignal, For } from "solid-js";
import { api } from "../shared/api";

const RANGES: Record<string, [number, number]> = {
  head_lr: [1828, 2298],
  head_ud: [2885, 3278],
  eye: [1960, 2130],
  jaw: [1534, 1728],
  brow: [2051, 2099],
};

export const ServoSliders: Component = () => {
  const [vals, setVals] = createSignal<Record<string, number>>({
    head_lr: 2063, head_ud: 3082, eye: 2045, jaw: 1728, brow: 2075,
  });

  let pending: ReturnType<typeof setTimeout> | undefined;

  const onDrag = (name: string, position: number) => {
    setVals((v) => ({ ...v, [name]: position }));
    if (pending) clearTimeout(pending);
    // Throttle preview to ~50ms
    pending = setTimeout(() => api.animatorPreview(name, position).catch(() => {}), 50);
  };

  const onCommit = async (name: string) => {
    try {
      await api.putSetting(`animator.${name}.default`, { value: vals()[name], value_type: "int" });
    } catch (e: any) {
      alert(e.message);
    }
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <h2 class="text-lg font-semibold mb-3">Servo preview</h2>
      <div class="space-y-3">
        <For each={Object.entries(RANGES)}>{([name, [lo, hi]]) => (
          <div>
            <div class="flex justify-between text-xs text-slate-400">
              <span class="font-mono">{name}</span>
              <span class="tabular-nums">{vals()[name]}</span>
            </div>
            <div class="flex gap-2">
              <input
                type="range" min={Math.min(lo, hi)} max={Math.max(lo, hi)}
                value={vals()[name]}
                onInput={(e) => onDrag(name, parseInt(e.currentTarget.value, 10))}
                class="flex-1"
              />
              <button class="text-xs px-2 rounded bg-slate-700 hover:bg-slate-600" onClick={() => onCommit(name)}>save</button>
            </div>
          </div>
        )}</For>
      </div>
    </section>
  );
};
