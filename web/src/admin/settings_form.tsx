import { Component, createSignal, onMount, For, Show, createMemo, onCleanup } from "solid-js";
import { api } from "../shared/api";
import { lsGet, lsRemove, lsSet, lsKeys } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

interface Row {
  key: string;
  value: string;
  value_type: string;
  description?: string | null;
  /** Last value confirmed by the server. Used for dirty detection + discard. */
  serverValue: string;
  /** Factory default from /api/settings/_defaults, if known. */
  factoryDefault?: string;
}

const SLIDER_HINTS: Record<string, { min: number; max: number; step?: number }> = {
  "speaker.volume":          { min: 0,    max: 100,   step: 1     },
  "agent.silence_threshold": { min: 0,    max: 5000,  step: 50    },
  "agent.silence_seconds":   { min: 0,    max: 5,     step: 0.1   },
  "tts.length_scale":        { min: 0.5,  max: 2.0,   step: 0.05  },
};

const DYNAMIC_OPTIONS: Record<string, () => Promise<string[]>> = {
  "agent.llm_model": async () => {
    const { models } = await api.listLlmModels();
    return models.map((m) => m.name);
  },
};

const DRAFT_PREFIX = "settings/draft/";

interface Props {
  onDraftCountChange?: () => void;
}

export const SettingsForm: Component<Props> = (props) => {
  const [rows, setRows] = createSignal<Row[]>([]);
  const [savingKey, setSavingKey] = createSignal<string | null>(null);
  const [dynamicOptions, setDynamicOptions] = createSignal<Record<string, string[]>>({});
  const [filter, setFilter] = createSignal("");

  const notifyDrafts = () => {
    props.onDraftCountChange?.();
    window.dispatchEvent(new CustomEvent("lafufu:drafts-changed"));
  };

  const isDirty = (r: Row) => r.value !== r.serverValue;
  const dirtyCount = createMemo(() => rows().filter(isDirty).length);

  const reload = async () => {
    const [data, defaults] = await Promise.all([
      api.listSettings(),
      api.listSettingDefaults().catch(() => [] as any),
    ]);
    const factoryMap = new Map<string, string>();
    for (const d of defaults as any[]) factoryMap.set(d.key, d.value);

    const hydrated: Row[] = (data as any[]).map((row) => {
      const draft = lsGet<string | null>(DRAFT_PREFIX + row.key, null);
      return {
        key: row.key,
        value: draft !== null ? draft : row.value,
        value_type: row.value_type,
        description: row.description,
        serverValue: row.value,
        factoryDefault: factoryMap.get(row.key),
      };
    });
    setRows(hydrated);
    notifyDrafts();
  };

  const loadDynamicOptions = async () => {
    const out: Record<string, string[]> = {};
    for (const [key, fetcher] of Object.entries(DYNAMIC_OPTIONS)) {
      try { out[key] = await fetcher(); }
      catch { /* falls back to free-text */ }
    }
    setDynamicOptions(out);
  };

  onMount(async () => {
    await Promise.all([reload(), loadDynamicOptions()]);
    const onWipe = () => reload();
    window.addEventListener("lafufu:drafts-wiped", onWipe);
    onCleanup(() => window.removeEventListener("lafufu:drafts-wiped", onWipe));
  });

  /** Update in-memory + persist to localStorage. */
  const update = (key: string, newValue: string) => {
    setRows((rs) =>
      rs.map((r) => {
        if (r.key !== key) return r;
        if (newValue === r.serverValue) lsRemove(DRAFT_PREFIX + key);
        else lsSet(DRAFT_PREFIX + key, newValue);
        return { ...r, value: newValue };
      }),
    );
    notifyDrafts();
  };

  const parseValue = (row: Row) => {
    switch (row.value_type) {
      case "json":  return JSON.parse(row.value);
      case "int":   return parseInt(row.value, 10);
      case "float": return parseFloat(row.value);
      case "bool":  return row.value === "true" || row.value === "1";
      default:      return row.value;
    }
  };

  const commit = async (row: Row) => {
    setSavingKey(row.key);
    try {
      await api.patchSetting(row.key, {
        value: parseValue(row),
        value_type: row.value_type,
      });
      setRows((rs) =>
        rs.map((r) => (r.key === row.key ? { ...r, serverValue: r.value } : r)),
      );
      lsRemove(DRAFT_PREFIX + row.key);
      notifyDrafts();
      toast.ok(`saved ${row.key}`, `value = ${row.value}`);
    } catch (e: any) {
      toast.err(`save ${row.key} failed`, e.message);
    } finally {
      setSavingKey(null);
    }
  };

  const discard = (row: Row) => {
    setRows((rs) =>
      rs.map((r) => (r.key === row.key ? { ...r, value: r.serverValue } : r)),
    );
    lsRemove(DRAFT_PREFIX + row.key);
    notifyDrafts();
    toast.info(`discarded ${row.key}`, `back to ${row.serverValue}`);
  };

  const resetField = async (row: Row) => {
    if (!row.factoryDefault) {
      toast.warn(`no factory default known for ${row.key}`);
      return;
    }
    if (row.factoryDefault === row.value && row.factoryDefault === row.serverValue) {
      toast.info(`${row.key} is already at default`);
      return;
    }
    update(row.key, row.factoryDefault);
    await commit({ ...row, value: row.factoryDefault });
  };

  const saveAllDirty = async () => {
    const dirty = rows().filter(isDirty);
    if (dirty.length === 0) { toast.info("nothing to save"); return; }
    let ok = 0, fail = 0;
    for (const r of dirty) {
      try {
        await api.patchSetting(r.key, { value: parseValue(r), value_type: r.value_type });
        lsRemove(DRAFT_PREFIX + r.key);
        ok++;
      } catch { fail++; }
    }
    await reload();
    if (fail === 0) toast.ok(`saved ${ok} setting${ok === 1 ? "" : "s"}`);
    else toast.warn(`saved ${ok}, failed ${fail}`, "check console for details");
  };

  const resetAllToDefaults = async () => {
    const targets = rows().filter((r) => r.factoryDefault !== undefined);
    if (targets.length === 0) { toast.warn("no factory defaults available"); return; }
    const changed = targets.filter((r) => r.factoryDefault !== r.serverValue);
    if (changed.length === 0) { toast.info("everything is already at default"); return; }
    if (!window.confirm(
      `Reset ${changed.length} setting${changed.length === 1 ? "" : "s"} to factory defaults?`
    )) return;

    let ok = 0, fail = 0;
    for (const r of changed) {
      try {
        await api.patchSetting(r.key, {
          value: r.value_type === "int"   ? parseInt(r.factoryDefault!, 10)
              :  r.value_type === "float" ? parseFloat(r.factoryDefault!)
              :  r.value_type === "bool"  ? r.factoryDefault === "true"
              :  r.value_type === "json"  ? JSON.parse(r.factoryDefault!)
              :  r.factoryDefault,
          value_type: r.value_type,
        });
        lsRemove(DRAFT_PREFIX + r.key);
        ok++;
      } catch { fail++; }
    }
    await reload();
    if (fail === 0) toast.ok(`reset ${ok} setting${ok === 1 ? "" : "s"}`);
    else toast.warn(`reset ${ok}, failed ${fail}`);
  };

  const widgetFor = (row: Row) => {
    const opts = dynamicOptions()[row.key];
    if (opts && opts.length > 0) {
      const known = opts.includes(row.value);
      return (
        <select
          class={`field ${isDirty(row) ? "field--dirty" : ""}`}
          style={{ flex: 1, cursor: "pointer" }}
          value={known ? row.value : ""}
          onChange={(e) => update(row.key, e.currentTarget.value)}
        >
          <Show when={!known}>
            <option value="" disabled>
              {row.value} (not in list)
            </option>
          </Show>
          <For each={opts}>{(o) => <option value={o}>{o}</option>}</For>
        </select>
      );
    }
    if (row.value_type === "bool") {
      const checked = row.value === "true" || row.value === "1";
      return (
        <label
          style={{
            flex: 1, display: "flex", "align-items": "center", gap: "10px", cursor: "pointer",
            padding: "6px 0",
          }}
        >
          <span
            onClick={() => update(row.key, checked ? "false" : "true")}
            style={{
              width: "40px", height: "22px",
              "border-radius": "999px",
              background: checked ? "var(--c-moss)" : "var(--c-shell)",
              border: `1px solid ${isDirty(row) ? "var(--c-amber)" : "var(--c-edge)"}`,
              position: "relative",
              transition: "background var(--t-fast)",
            }}
          >
            <span
              style={{
                position: "absolute",
                top: "1px",
                left: checked ? "19px" : "1px",
                width: "18px", height: "18px",
                "border-radius": "50%",
                background: "var(--c-bone)",
                transition: "left var(--t-fast)",
                "box-shadow": "0 1px 3px rgba(0,0,0,.4)",
              }}
            />
          </span>
          <span
            class="f-mono"
            style={{ "font-size": "12px", color: isDirty(row) ? "var(--c-amber)" : "var(--c-mist)" }}
          >
            {checked ? "true" : "false"}
          </span>
        </label>
      );
    }
    if (row.value_type === "int" || row.value_type === "float") {
      const hint = SLIDER_HINTS[row.key];
      const step = row.value_type === "int" ? 1 : "any";
      if (hint) {
        return (
          <div style={{ flex: 1, display: "flex", gap: "10px", "align-items": "center" }}>
            <input
              type="range" class="slider"
              min={hint.min} max={hint.max} step={hint.step ?? step}
              value={row.value}
              style={{ flex: 1 }}
              onInput={(e) => update(row.key, e.currentTarget.value)}
            />
            <input
              type="number"
              class={`field f-num ${isDirty(row) ? "field--dirty" : ""}`}
              style={{ width: "82px", "text-align": "right" }}
              min={hint.min} max={hint.max} step={hint.step ?? step}
              value={row.value}
              onInput={(e) => update(row.key, e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && isDirty(row) && commit(row)}
            />
          </div>
        );
      }
      return (
        <input
          type="number" step={step}
          class={`field ${isDirty(row) ? "field--dirty" : ""}`}
          style={{ flex: 1 }}
          value={row.value}
          onInput={(e) => update(row.key, e.currentTarget.value)}
          onKeyDown={(e) => e.key === "Enter" && isDirty(row) && commit(row)}
        />
      );
    }
    if (row.value_type === "json") {
      return (
        <textarea
          rows="2"
          class={`field ${isDirty(row) ? "field--dirty" : ""}`}
          style={{ flex: 1, resize: "vertical", "min-height": "44px" }}
          value={row.value}
          onInput={(e) => update(row.key, e.currentTarget.value)}
        />
      );
    }
    if (row.value.length > 80) {
      return (
        <textarea
          rows="3"
          class={`field ${isDirty(row) ? "field--dirty" : ""}`}
          style={{ flex: 1, resize: "vertical", "font-family": "var(--f-sans)" }}
          value={row.value}
          onInput={(e) => update(row.key, e.currentTarget.value)}
        />
      );
    }
    return (
      <input
        type="text"
        class={`field ${isDirty(row) ? "field--dirty" : ""}`}
        style={{ flex: 1 }}
        value={row.value}
        onInput={(e) => update(row.key, e.currentTarget.value)}
        onKeyDown={(e) => e.key === "Enter" && isDirty(row) && commit(row)}
      />
    );
  };

  const filtered = createMemo(() => {
    const q = filter().trim().toLowerCase();
    if (!q) return rows();
    return rows().filter((r) =>
      r.key.toLowerCase().includes(q) ||
      (r.description ?? "").toLowerCase().includes(q),
    );
  });

  return (
    <Panel
      title="Settings"
      eyebrow="tunables · per-key drafts → localStorage"
      accent="var(--c-amber)"
      height="62vh"
      actions={
        <>
          <Show when={dirtyCount() > 0}>
            <button class="btn btn--primary btn--tiny" onClick={saveAllDirty}>
              save all ({dirtyCount()})
            </button>
          </Show>
          <button class="btn btn--ghost btn--tiny" onClick={reload}>
            refresh
          </button>
          <button
            class="btn btn--coral btn--tiny"
            onClick={resetAllToDefaults}
            title="Reset every setting to its factory default"
          >
            reset all
          </button>
        </>
      }
    >
      <div style={{ "margin-bottom": "14px" }}>
        <input
          type="text"
          class="field"
          style={{ width: "100%" }}
          placeholder="filter by key or description…"
          value={filter()}
          onInput={(e) => setFilter(e.currentTarget.value)}
        />
      </div>

      <div style={{ display: "flex", "flex-direction": "column", gap: "16px" }}>
        <For each={filtered()}>
          {(row) => {
            const dirty = () => isDirty(row);
            const atDefault = () =>
              row.factoryDefault !== undefined && row.serverValue === row.factoryDefault;
            return (
              <div
                style={{
                  padding: "12px 14px",
                  "border-radius": "14px",
                  background: dirty() ? "rgba(212, 162, 89, 0.06)" : "rgba(243, 236, 220, 0.02)",
                  border: `1px solid ${dirty() ? "rgba(212,162,89,.3)" : "var(--c-edge)"}`,
                  transition: "background var(--t-fast), border-color var(--t-fast)",
                }}
              >
                <div style={{ display: "flex", "align-items": "center", gap: "8px", "margin-bottom": "10px", "flex-wrap": "wrap" }}>
                  <code
                    class="f-mono"
                    style={{
                      "font-size": "12px",
                      color: "var(--c-cream)",
                      background: "var(--c-shell)",
                      padding: "2px 8px",
                      "border-radius": "6px",
                      border: "1px solid var(--c-edge)",
                    }}
                  >
                    {row.key}
                  </code>
                  <span
                    class="f-mono"
                    style={{
                      "font-size": "10px",
                      color: "var(--c-stone)",
                      "letter-spacing": ".08em",
                      "text-transform": "uppercase",
                    }}
                  >
                    {row.value_type}
                  </span>
                  <Show when={atDefault()}>
                    <span
                      class="f-mono"
                      style={{
                        "font-size": "10px",
                        color: "var(--c-moss)",
                        "letter-spacing": ".08em",
                      }}
                    >
                      ◦ default
                    </span>
                  </Show>
                  <Show when={dirty()}>
                    <span
                      class="f-mono"
                      style={{
                        "font-size": "10px",
                        color: "var(--c-amber)",
                        "letter-spacing": ".08em",
                        animation: "breathe 2.4s ease-in-out infinite",
                      }}
                    >
                      ● draft pending
                    </span>
                  </Show>
                  <div style={{ flex: 1 }} />
                  <Show when={dirty()}>
                    <button class="btn btn--ghost btn--micro" onClick={() => discard(row)}>
                      discard
                    </button>
                  </Show>
                  <Show when={row.factoryDefault !== undefined && !atDefault()}>
                    <button
                      class="btn btn--ghost btn--micro"
                      onClick={() => resetField(row)}
                      title={`Reset to ${row.factoryDefault}`}
                    >
                      reset
                    </button>
                  </Show>
                  <button
                    class="btn btn--micro"
                    classList={{ "btn--primary": dirty() }}
                    disabled={!dirty() || savingKey() === row.key}
                    onClick={() => commit(row)}
                  >
                    {savingKey() === row.key ? "…" : "save"}
                  </button>
                </div>

                <div style={{ display: "flex", "align-items": "center", gap: "10px" }}>
                  {widgetFor(row)}
                </div>

                <Show when={row.description}>
                  <div
                    style={{
                      "font-size": "12px",
                      "line-height": 1.4,
                      color: "var(--c-stone)",
                      "margin-top": "8px",
                      "font-style": "italic",
                    }}
                  >
                    {row.description}
                  </div>
                </Show>

                <Show when={dirty()}>
                  <div
                    class="f-mono"
                    style={{
                      "font-size": "10px",
                      color: "var(--c-stone)",
                      "margin-top": "6px",
                    }}
                  >
                    server: <span style={{ color: "var(--c-mist)" }}>{row.serverValue}</span>
                    <span style={{ margin: "0 8px" }}>→</span>
                    draft: <span style={{ color: "var(--c-amber)" }}>{row.value}</span>
                  </div>
                </Show>
              </div>
            );
          }}
        </For>

        <Show when={filtered().length === 0}>
          <div
            style={{
              padding: "24px",
              "text-align": "center",
              color: "var(--c-stone)",
              "font-style": "italic",
            }}
          >
            no settings matching "{filter()}"
          </div>
        </Show>
      </div>
    </Panel>
  );
};

void lsKeys;
