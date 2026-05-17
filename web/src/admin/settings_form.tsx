import { Component, createSignal, onMount, For } from "solid-js";
import { api } from "../shared/api";

interface Row {
  key: string;
  value: string;
  value_type: string;
}

export const SettingsForm: Component = () => {
  const [rows, setRows] = createSignal<Row[]>([]);
  const [dirty, setDirty] = createSignal<Set<string>>(new Set());

  const reload = async () => {
    const data = await api.listSettings();
    setRows(data as Row[]);
  };

  onMount(reload);

  const update = (key: string, newValue: string) => {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, value: newValue } : r)));
    setDirty((d) => new Set(d).add(key));
  };

  const commit = async (row: Row) => {
    const parsed = row.value_type === "json" ? JSON.parse(row.value)
      : row.value_type === "int" ? parseInt(row.value, 10)
      : row.value_type === "float" ? parseFloat(row.value)
      : row.value_type === "bool" ? row.value === "true"
      : row.value;
    await api.patchSetting(row.key, { value: parsed, value_type: row.value_type });
    setDirty((d) => { const c = new Set(d); c.delete(row.key); return c; });
  };

  return (
    <section class="rounded-lg bg-slate-900 p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold">Settings</h2>
        <button class="text-xs text-slate-400 hover:text-slate-200" onClick={reload}>refresh</button>
      </div>
      <div class="space-y-2 max-h-[60vh] overflow-y-auto">
        <For each={rows()}>{(row) => (
          <div class="flex items-center gap-2">
            <label class="font-mono text-xs text-slate-400 w-1/2 truncate" title={row.key}>{row.key}</label>
            <input
              class={`flex-1 bg-slate-800 border ${dirty().has(row.key) ? "border-amber-500" : "border-slate-700"} rounded px-2 py-1 text-sm`}
              value={row.value}
              onInput={(e) => update(row.key, e.currentTarget.value)}
            />
            <button
              class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-30"
              disabled={!dirty().has(row.key)}
              onClick={() => commit(row).catch((e) => alert(e.message))}
            >save</button>
          </div>
        )}</For>
        {rows().length === 0 && <div class="text-slate-500 text-sm">No settings yet.</div>}
      </div>
    </section>
  );
};
