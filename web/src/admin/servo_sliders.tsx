import { Component, createSignal, onMount, For, Show, createMemo } from "solid-js";
import { api } from "../shared/api";
import { lsGet, lsSet, lsRemove } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

const RANGES: Array<{ name: string; lo: number; hi: number }> = [
  { name: "head_lr", lo: 1828, hi: 2298 },
  { name: "head_ud", lo: 2885, hi: 3278 },
  { name: "eye",     lo: 1960, hi: 2130 },
  { name: "jaw",     lo: 1534, hi: 1728 },
  { name: "brow",    lo: 2051, hi: 2099 },
];

const FACTORY_DEFAULTS: Record<string, number> = {
  head_lr: 2063, head_ud: 3082, eye: 2045, jaw: 1728, brow: 2075,
};

const DRAFT_KEY = "sliders/values";

export const ServoSliders: Component = () => {
  // Live preview values — driven by sliders, throttled to the server.
  const [vals, setVals] = createSignal<Record<string, number>>({ ...FACTORY_DEFAULTS });
  // Last-saved values per key (default position in the DB). Loaded from localStorage as a cache.
  const [savedVals, setSavedVals] = createSignal<Record<string, number>>({});
  const [saving, setSaving] = createSignal<string | null>(null);

  onMount(() => {
    // Hydrate from localStorage cache of saved values.
    const cached = lsGet<Record<string, number>>(DRAFT_KEY, {});
    if (Object.keys(cached).length > 0) {
      setVals((v) => ({ ...v, ...cached }));
      setSavedVals(cached);
    }
  });

  let pending: ReturnType<typeof setTimeout> | undefined;
  const onDrag = (name: string, position: number) => {
    setVals((v) => ({ ...v, [name]: position }));
    if (pending) clearTimeout(pending);
    pending = setTimeout(() => api.animatorPreview(name, position).catch(() => {}), 40);
  };

  const onCommit = async (name: string) => {
    setSaving(name);
    try {
      const v = vals()[name];
      await api.putSetting(`animator.${name}.default`, { value: v, value_type: "int" });
      setSavedVals((s) => ({ ...s, [name]: v }));
      lsSet(DRAFT_KEY, { ...savedVals(), [name]: v });
      toast.ok(`saved ${name}`, `default = ${v}`);
    } catch (e: any) {
      toast.err(`save ${name} failed`, e.message);
    } finally {
      setSaving(null);
    }
  };

  const onReset = async (name: string) => {
    const def = FACTORY_DEFAULTS[name];
    setVals((v) => ({ ...v, [name]: def }));
    api.animatorPreview(name, def).catch(() => {});
    try {
      await api.putSetting(`animator.${name}.default`, { value: def, value_type: "int" });
      setSavedVals((s) => ({ ...s, [name]: def }));
      lsSet(DRAFT_KEY, { ...savedVals(), [name]: def });
      toast.ok(`reset ${name}`, `default = ${def}`);
    } catch (e: any) {
      toast.err(`reset ${name} failed`, e.message);
    }
  };

  const onResetAll = async () => {
    if (!window.confirm("Reset all 5 servo defaults to factory mid-positions?")) return;
    let ok = 0, fail = 0;
    for (const r of RANGES) {
      try {
        const def = FACTORY_DEFAULTS[r.name];
        setVals((v) => ({ ...v, [r.name]: def }));
        await api.putSetting(`animator.${r.name}.default`, { value: def, value_type: "int" });
        ok++;
      } catch { fail++; }
    }
    lsRemove(DRAFT_KEY);
    setSavedVals(FACTORY_DEFAULTS);
    if (fail === 0) toast.ok(`reset ${ok} servo${ok === 1 ? "" : "s"}`);
    else toast.warn(`reset ${ok}, failed ${fail}`);
  };

  const dirty = (name: string) =>
    vals()[name] !== (savedVals()[name] ?? FACTORY_DEFAULTS[name]);
  const dirtyCount = createMemo(() => RANGES.filter((r) => dirty(r.name)).length);

  return (
    <Panel
      title="Servo preview"
      eyebrow="drag to preview · save sets the default pose"
      accent="var(--c-coral)"
      actions={
        <>
          <Show when={dirtyCount() > 0}>
            <span
              class="f-mono"
              style={{
                "font-size": "10px",
                color: "var(--c-amber)",
                "letter-spacing": ".08em",
              }}
            >
              {dirtyCount()} unsaved
            </span>
          </Show>
          <button class="btn btn--coral btn--tiny" onClick={onResetAll}>
            reset all
          </button>
        </>
      }
    >
      <div style={{ display: "flex", "flex-direction": "column", gap: "14px" }}>
        <For each={RANGES}>
          {({ name, lo, hi }) => {
            const v = () => vals()[name];
            const frac = () => Math.max(0, Math.min(1, (v() - lo) / (hi - lo)));
            return (
              <div>
                <div
                  style={{
                    display: "flex",
                    "justify-content": "space-between",
                    "align-items": "center",
                    "margin-bottom": "6px",
                  }}
                >
                  <span
                    class="f-mono"
                    style={{
                      "font-size": "11px",
                      color: dirty(name) ? "var(--c-amber)" : "var(--c-mist)",
                      "letter-spacing": ".06em",
                    }}
                  >
                    {name}
                  </span>
                  <div style={{ display: "flex", "align-items": "center", gap: "8px" }}>
                    <span
                      class="f-mono f-num"
                      style={{
                        "font-size": "13px",
                        color: dirty(name) ? "var(--c-amber)" : "var(--c-bone)",
                        "min-width": "44px",
                        "text-align": "right",
                      }}
                    >
                      {v()}
                    </span>
                    <Show when={dirty(name)}>
                      <button
                        class="btn btn--ghost btn--micro"
                        onClick={() => onReset(name)}
                      >
                        reset
                      </button>
                    </Show>
                    <button
                      class="btn btn--micro"
                      classList={{ "btn--primary": dirty(name) }}
                      disabled={!dirty(name) || saving() === name}
                      onClick={() => onCommit(name)}
                    >
                      {saving() === name ? "…" : "save"}
                    </button>
                  </div>
                </div>
                <div style={{ position: "relative" }}>
                  <input
                    type="range"
                    class="slider"
                    style={{ width: "100%" }}
                    min={Math.min(lo, hi)} max={Math.max(lo, hi)}
                    value={v()}
                    onInput={(e) => onDrag(name, parseInt(e.currentTarget.value, 10))}
                  />
                </div>
                <div
                  style={{
                    display: "flex",
                    "justify-content": "space-between",
                    "margin-top": "4px",
                  }}
                  class="f-mono"
                >
                  <span style={{ "font-size": "9px", color: "var(--c-stone)" }}>{lo}</span>
                  <span
                    style={{
                      "font-size": "9px",
                      color: "var(--c-stone)",
                      opacity: .5,
                    }}
                  >
                    {(frac() * 100).toFixed(0)}%
                  </span>
                  <span style={{ "font-size": "9px", color: "var(--c-stone)" }}>{hi}</span>
                </div>
              </div>
            );
          }}
        </For>
      </div>
    </Panel>
  );
};
