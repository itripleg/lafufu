import { Component, createSignal, onMount, For, Show } from "solid-js";
import { api } from "../shared/api";

interface Row {
  key: string;
  value: string;
  value_type: string;
  description?: string | null;
}

// Per-key UI hints (slider ranges etc.) for known settings. Unknown keys fall
// back to a number input with no min/max.
const SLIDER_HINTS: Record<string, { min: number; max: number; step?: number }> = {
  "speaker.volume": { min: 0, max: 100, step: 1 },
  "agent.silence_threshold": { min: 0, max: 5000, step: 50 },
  "agent.silence_seconds": { min: 0, max: 5, step: 0.1 },
  "tts.length_scale": { min: 0.5, max: 2.0, step: 0.05 },
};

export const SettingsForm: Component = () => {
  const [rows, setRows] = createSignal<Row[]>([]);
  const [dirty, setDirty] = createSignal<Set<string>>(new Set());
  const [savingKey, setSavingKey] = createSignal<string | null>(null);

  const reload = async () => {
    const data = await api.listSettings();
    setRows(data as Row[]);
  };

  onMount(reload);

  const update = (key: string, newValue: string) => {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, value: newValue } : r)));
    setDirty((d) => new Set(d).add(key));
  };

  const parseValue = (row: Row) => {
    switch (row.value_type) {
      case "json":
        return JSON.parse(row.value);
      case "int":
        return parseInt(row.value, 10);
      case "float":
        return parseFloat(row.value);
      case "bool":
        return row.value === "true" || row.value === "1";
      default:
        return row.value;
    }
  };

  const commit = async (row: Row) => {
    setSavingKey(row.key);
    try {
      await api.patchSetting(row.key, {
        value: parseValue(row),
        value_type: row.value_type,
      });
      setDirty((d) => {
        const c = new Set(d);
        c.delete(row.key);
        return c;
      });
    } catch (e: any) {
      alert(`save ${row.key} failed: ${e.message}`);
    } finally {
      setSavingKey(null);
    }
  };

  const inputClass = (key: string) =>
    `flex-1 bg-slate-800 border ${
      dirty().has(key) ? "border-amber-500" : "border-slate-700"
    } rounded px-2 py-1 text-sm font-mono`;

  const handleKeyDown = (row: Row) => (e: KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (dirty().has(row.key)) commit(row);
    }
  };

  // Renders the input widget for a row based on its value_type.
  const renderWidget = (row: Row) => {
    if (row.value_type === "bool") {
      const checked = () => row.value === "true" || row.value === "1";
      return (
        <label class="flex-1 flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            class={`w-4 h-4 rounded border ${
              dirty().has(row.key) ? "border-amber-500" : "border-slate-700"
            }`}
            checked={checked()}
            onChange={(e) => update(row.key, e.currentTarget.checked ? "true" : "false")}
          />
          <span class={dirty().has(row.key) ? "text-amber-300" : "text-slate-400"}>
            {checked() ? "true" : "false"}
          </span>
        </label>
      );
    }

    if (row.value_type === "int" || row.value_type === "float") {
      const hint = SLIDER_HINTS[row.key];
      const step = row.value_type === "int" ? 1 : "any";
      if (hint) {
        // Slider + number input pair when bounds are known
        return (
          <div class="flex-1 flex items-center gap-2">
            <input
              type="range"
              min={hint.min}
              max={hint.max}
              step={hint.step ?? step}
              value={row.value}
              class="flex-1"
              onInput={(e) => update(row.key, e.currentTarget.value)}
            />
            <input
              type="number"
              min={hint.min}
              max={hint.max}
              step={hint.step ?? step}
              value={row.value}
              class={`w-20 ${inputClass(row.key)} text-right`}
              onInput={(e) => update(row.key, e.currentTarget.value)}
              onKeyDown={handleKeyDown(row)}
            />
          </div>
        );
      }
      return (
        <input
          type="number"
          step={step}
          value={row.value}
          class={inputClass(row.key)}
          onInput={(e) => update(row.key, e.currentTarget.value)}
          onKeyDown={handleKeyDown(row)}
        />
      );
    }

    if (row.value_type === "json") {
      return (
        <textarea
          rows="2"
          class={inputClass(row.key) + " resize-y"}
          value={row.value}
          onInput={(e) => update(row.key, e.currentTarget.value)}
        />
      );
    }

    // string (default)
    return (
      <input
        type="text"
        value={row.value}
        class={inputClass(row.key)}
        onInput={(e) => update(row.key, e.currentTarget.value)}
        onKeyDown={handleKeyDown(row)}
      />
    );
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold">Settings</h2>
        <button
          class="text-xs text-slate-400 hover:text-slate-200"
          onClick={reload}
        >
          refresh
        </button>
      </div>
      <div class="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
        <For each={rows()}>
          {(row) => (
            <div>
              <div class="flex items-start gap-2">
                <div
                  class="font-mono text-xs text-slate-400 w-44 shrink-0 pt-1 truncate"
                  title={row.key}
                >
                  {row.key}
                </div>
                {renderWidget(row)}
                <button
                  class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-30 shrink-0"
                  disabled={!dirty().has(row.key) || savingKey() === row.key}
                  onClick={() => commit(row)}
                  title="Save (or press Enter in the field)"
                >
                  {savingKey() === row.key ? "..." : "save"}
                </button>
              </div>
              <Show when={row.description}>
                <div class="text-xs text-slate-500 mt-1 ml-44 pl-2">{row.description}</div>
              </Show>
            </div>
          )}
        </For>
        {rows().length === 0 && (
          <div class="text-slate-500 text-sm">No settings yet.</div>
        )}
      </div>
    </section>
  );
};
