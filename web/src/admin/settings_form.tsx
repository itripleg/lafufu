import { Component, createSignal, onMount, For, Show, createMemo, onCleanup } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import { api } from "../shared/api";
import { lsGet, lsRemove, lsSet, lsKeys } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { Panel } from "./panel";
import { LetterheadCard, ComposeFortuneCard } from "./printer_card";

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
  "speaker.volume":            { min: 0,    max: 100,   step: 1     },
  "agent.silence_threshold":   { min: 0,    max: 5000,  step: 50    },
  "agent.silence_seconds":     { min: 0,    max: 5,     step: 0.1   },
  "tts.length_scale":          { min: 0.5,  max: 2.0,   step: 0.05  },
  // Printer positioning — Phomemo driver native ranges.
  "printer.adjust_vertical":   { min: -20,  max: 20,    step: 1     },
  "printer.adjust_horizontal": { min: -20,  max: 20,    step: 1     },
  "printer.feed_offset":       { min: -20,  max: 20,    step: 1     },
  "printer.rotate":            { min: 0,    max: 3,     step: 1     },
  "printer.scale_pct":         { min: 25,   max: 200,   step: 5     },
  "printer.dead_zone_top_mm":  { min: 0,    max: 15,    step: 1     },
  "printer.dead_zone_bottom_mm": { min: 0,  max: 15,    step: 1     },
  // Trigger / wake-word numeric tunables.
  "agent.trigger.rounds":     { min: 1,    max: 10,   step: 1     },
  "agent.wakeword.threshold": { min: 0.0,  max: 1.0,  step: 0.05  },
  // Servo defaults — ranges mirror packages/animator/.../pose.py CLAMP table.
  // Moving these sliders moves the robot LIVE — descriptions warn the operator.
  "animator.head_lr.default": { min: 1828, max: 2298, step: 1     },
  "animator.head_ud.default": { min: 2885, max: 3278, step: 1     },
  "animator.eye.default":     { min: 1995, max: 2085, step: 1     },
  "animator.jaw.default":     { min: 1594, max: 1811, step: 1     },
  "animator.brow.default":    { min: 2056, max: 2087, step: 1     },
  // Lipsync — speed (attack/release) + timing offset.
  "animator.lipsync.attack_ms":  { min: 5, max: 200, step: 1 },
  "animator.lipsync.release_ms": { min: 5, max: 400, step: 1 },
  "animator.lipsync.offset_ms":  { min: 0, max: 500, step: 5 },
};

// Servo-default setting keys → the servo name the /preview API expects.
// Hand-dragging one of these sliders fires a coalesced preview so Lafufu
// moves under your hands (matches FramesSection's editor UX). The DB write
// still happens on save — preview only nudges the live target_pose.
const PREVIEW_SERVO: Record<string, string> = {
  "animator.head_lr.default": "head_lr",
  "animator.head_ud.default": "head_ud",
  "animator.eye.default":     "eye",
  "animator.jaw.default":     "jaw",
  "animator.brow.default":    "brow",
};

/** A dropdown option: stored value (saved to DB) + display label (what the
 *  operator sees). When label is omitted, value doubles as the label. */
export type OptionEntry = string | { value: string; label: string };

const DYNAMIC_OPTIONS: Record<string, () => Promise<OptionEntry[]>> = {
  "agent.llm_model": async () => {
    const { models } = await api.listLlmModels();
    return models.map((m) => m.name);
  },
  "agent.stt_backend": async () => {
    const { backends } = await api.listSttBackends();
    return backends.filter((b) => b.available).map((b) => b.id);
  },
  "agent.voice_model": async () => {
    const { voices } = await api.listVoices();
    // Piper needs both the .onnx and the .onnx.json — drop voices that are
    // missing the companion config so the operator can't select something
    // that would fail to load.
    return voices.filter((v) => v.has_config).map((v) => v.name);
  },
  // Canonical Whisper / faster-whisper model identifiers. The list is fixed
  // (neither backend exposes "list installed"; they download lazily) but the
  // backend reports which are on disk + their download sizes so the operator
  // sees the cost of picking a model that isn't cached.
  "agent.whisper_model": async () => {
    const { models } = await api.listWhisperModels();
    return models.map((m) => ({
      value: m.name,
      label: m.cached
        ? `${m.name} — ${m.size_mb} MB cached`
        : `${m.name} — ${m.size_mb} MB (will download)`,
    }));
  },
  // Trigger mode + wake-word config — all enums hardcoded since these are
  // tiny fixed sets the backend doesn't enumerate.
  "agent.interaction_mode": async () => ["continuous", "trigger"],
  "agent.trigger.emotion": async () => [
    "happy", "sad", "angry", "surprised", "neutral", "agree", "disagree",
  ],
  "agent.trigger.print_mode": async () => ["none", "auto", "ask"],
  // The trained "hey lafufu" model ships in the repo at assets/wakeword/lafufu.onnx
  // and is the bootstrap default. Other entries are openwakeword's bundled
  // fallbacks. The agent's resolve_model_ref() anchors the relative path to the
  // workspace root so this string works regardless of CWD. TODO: switch to a
  // /api/agent/wakeword-models endpoint that enumerates assets/wakeword/*.onnx
  // alongside the bundled names, like /voices does.
  "agent.wakeword.model": async () => [
    { value: "assets/wakeword/lafufu.onnx", label: "hey lafufu (custom)" },
    "hey_jarvis_v0.1",
    "alexa_v0.1",
    "hey_mycroft_v0.1",
    "hey_rhasspy_v0.1",
    "timer_v0.1",
    "weather_v0.1",
  ],
  // Mic device picker — backend enumerates PyAudio's input devices,
  // first entry is always the "auto" sentinel.
  "agent.input_device": async () => {
    const { devices } = await api.listInputDevices();
    return devices.map((d) => ({ value: d.name, label: d.label }));
  },
};

/** Pure helper: split an OptionEntry into (value, label). */
export const optionParts = (o: OptionEntry): { value: string; label: string } =>
  typeof o === "string" ? { value: o, label: o } : { value: o.value, label: o.label };

const DRAFT_PREFIX = "settings/draft/";

type Tab = "agent" | "animator" | "audio" | "printer" | "other";

const TABS: Array<{ id: Tab; label: string; hint: string }> = [
  { id: "agent",    label: "agent",    hint: "voice loop · trigger · wake word · mic" },
  { id: "animator", label: "animator", hint: "idle animation · servo defaults" },
  { id: "audio",    label: "audio",    hint: "speaker · TTS" },
  { id: "printer",  label: "printer",  hint: "auto-print · letterhead" },
  { id: "other",    label: "other",    hint: "uncategorised" },
];

export function categoryOf(key: string): Tab {
  // Audio tab is the one cross-namespace exception — speaker.* + tts.* both
  // live there because operators think of them together (volume + voice
  // playback speed).
  if (key.startsWith("speaker.") || key.startsWith("tts.")) return "audio";
  const prefix = key.split(".", 1)[0];
  if (prefix === "agent" || prefix === "animator" || prefix === "printer") {
    return prefix;
  }
  return "other";
}

interface Props {
  onDraftCountChange?: () => void;
}

export const SettingsForm: Component<Props> = (props) => {
  // Stored as a store (not signal) so granular property updates don't change
  // row identity. <For> reconciles by reference; a fresh object on every
  // keystroke would destroy and rebuild the row's DOM — that's what made
  // number inputs lose focus / refuse input mid-typing.
  const [rows, setRows] = createStore<Row[]>([]);
  const [savingKey, setSavingKey] = createSignal<string | null>(null);
  const [dynamicOptions, setDynamicOptions] = createSignal<Record<string, OptionEntry[]>>({});
  const [filter, setFilter] = createSignal("");
  const [tab, setTab] = createSignal<Tab>(lsGet<Tab>("settings/tab", "audio"));
  const setActiveTab = (t: Tab) => { setTab(t); lsSet("settings/tab", t); };

  const notifyDrafts = () => {
    props.onDraftCountChange?.();
    window.dispatchEvent(new CustomEvent("lafufu:drafts-changed"));
  };

  const isDirty = (r: Row) => r.value !== r.serverValue;
  const dirtyCount = createMemo(() => rows.filter(isDirty).length);

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
    // reconcile keys per-row so existing row proxies stay identity-stable —
    // otherwise <For> would tear down every row on each /api/settings poll.
    setRows(reconcile(hydrated, { key: "key", merge: false }));
    notifyDrafts();
  };

  const loadDynamicOptions = async () => {
    const out: Record<string, OptionEntry[]> = {};
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

  // Throttle live-preview POSTs to ~40 ms during slider drag — matches
  // FramesSection so two open editors share the same feel. Coalesces to the
  // most recent (servo, value) pair so a fast drag never queues stale frames.
  let previewTimer: number | undefined;
  let pendingPreview: { servo: string; value: number } | null = null;
  const schedulePreview = (servo: string, value: number) => {
    pendingPreview = { servo, value };
    if (previewTimer != null) return;
    previewTimer = window.setTimeout(async () => {
      previewTimer = undefined;
      const p = pendingPreview;
      pendingPreview = null;
      if (p) {
        try { await api.animatorPreview(p.servo, p.value); } catch { /* drop */ }
      }
    }, 40);
  };

  /** Update in-memory + persist to localStorage. */
  const update = (key: string, newValue: string) => {
    const idx = rows.findIndex((r) => r.key === key);
    if (idx < 0) return;
    if (newValue === rows[idx].serverValue) lsRemove(DRAFT_PREFIX + key);
    else lsSet(DRAFT_PREFIX + key, newValue);
    setRows(idx, "value", newValue);
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
      const idx = rows.findIndex((r) => r.key === row.key);
      if (idx >= 0) setRows(idx, "serverValue", rows[idx].value);
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
    const idx = rows.findIndex((r) => r.key === row.key);
    if (idx >= 0) setRows(idx, "value", rows[idx].serverValue);
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
    const dirty = rows.filter(isDirty);
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
    const targets = rows.filter((r) => r.factoryDefault !== undefined);
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

  // Wrapping widgetFor in a Component breaks Solid's auto-memoization of
  // `{widgetFor(row)}` inside JSX — without this, every keystroke triggers
  // re-evaluation of the whole branch and remounts the input element, killing
  // focus. The Component boundary makes the call a one-shot at mount and
  // delegates further reactivity to per-attribute bindings.
  const Widget = (props: { row: Row }) => widgetFor(props.row);

  const widgetFor = (row: Row) => {
    const opts = dynamicOptions()[row.key];
    if (opts && opts.length > 0) {
      const known = opts.some((o) => optionParts(o).value === row.value);
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
          <For each={opts}>
            {(o) => {
              const { value, label } = optionParts(o);
              return <option value={value}>{label}</option>;
            }}
          </For>
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
      // type="text" + inputMode keeps partial input like "1." from being
      // wiped by browser number normalization while still showing the right
      // virtual keyboard on mobile. Validation happens at commit (parseValue).
      const inputMode = row.value_type === "float" ? "decimal" : "numeric";
      if (hint) {
        // Compact bound labels flanking the slider — operators can see
        // the legal range at a glance (matches the hardware clamp for servo
        // defaults, the API contract for thresholds, etc.).
        const boundStyle = {
          "font-size": "10px",
          color: "var(--c-stone)",
          "min-width": "32px",
          "text-align": "center" as const,
          "font-variant-numeric": "tabular-nums",
        };
        return (
          <div style={{ flex: 1, display: "flex", gap: "8px", "align-items": "center" }}>
            <span class="f-mono" style={boundStyle}>{hint.min}</span>
            <input
              type="range" class="slider"
              min={hint.min} max={hint.max} step={hint.step ?? step}
              value={row.value}
              style={{ flex: 1 }}
              onInput={(e) => {
                const v = e.currentTarget.value;
                update(row.key, v);
                const servo = PREVIEW_SERVO[row.key];
                if (servo) schedulePreview(servo, parseInt(v, 10));
              }}
            />
            <span class="f-mono" style={boundStyle}>{hint.max}</span>
            <input
              type="text"
              inputMode={inputMode}
              class={`field f-num ${isDirty(row) ? "field--dirty" : ""}`}
              style={{ width: "82px", "text-align": "right" }}
              value={row.value}
              onInput={(e) => update(row.key, e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && isDirty(row) && commit(row)}
            />
          </div>
        );
      }
      return (
        <input
          type="text"
          inputMode={inputMode}
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
      // Long strings (agent.system_prompt, etc.) — give them enough vertical
      // room to read/edit without scrolling. resize: vertical lets the
      // operator stretch further when needed.
      return (
        <textarea
          rows="10"
          class={`field ${isDirty(row) ? "field--dirty" : ""}`}
          style={{ flex: 1, resize: "vertical", "font-family": "var(--f-sans)", "min-height": "200px" }}
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

  const matchesQuery = (r: Row, q: string) =>
    r.key.toLowerCase().includes(q) ||
    (r.description ?? "").toLowerCase().includes(q);

  // Per-tab counts. `dirty` = unsaved drafts in that category; `matches` =
  // keys matching the current search query (0 when the search box is empty).
  const countsByTab = createMemo(() => {
    const q = filter().trim().toLowerCase();
    const out: Record<Tab, { dirty: number; matches: number }> = {
      agent: { dirty: 0, matches: 0 },
      animator: { dirty: 0, matches: 0 },
      audio: { dirty: 0, matches: 0 },
      printer: { dirty: 0, matches: 0 },
      other: { dirty: 0, matches: 0 },
    };
    for (const r of rows) {
      const c = categoryOf(r.key);
      if (isDirty(r)) out[c].dirty++;
      if (q && matchesQuery(r, q)) out[c].matches++;
    }
    return out;
  });

  // Hide tabs that have zero settings (currently 'other' — the bootstrap
  // doesn't seed any rows that fall outside the agent/animator/audio/printer
  // namespaces). If a future setting lands in 'other', the tab reappears
  // automatically without any code change here.
  const visibleTabs = createMemo(() => {
    const present = new Set<Tab>();
    for (const r of rows) present.add(categoryOf(r.key));
    return TABS.filter((t) => present.has(t.id));
  });

  const filtered = createMemo(() => {
    const q = filter().trim().toLowerCase();
    // Empty query → scope to the active tab. Non-empty → global search:
    // match across every category so a search finds settings regardless of
    // which tab is selected.
    if (!q) return rows.filter((r) => categoryOf(r.key) === tab());
    return rows.filter((r) => matchesQuery(r, q));
  });

  return (
    <Panel
      title="Settings"
      eyebrow="tunables · per-key drafts → localStorage"
      accent="var(--c-amber)"
      height="62vh"
      style={{ "min-height": "62vh" }}
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
      {/* Sticky header — tab bar + search input stay visible while scrolling
          through the settings list. The individual chrome on the tab bar and
          search input already provides a solid surface, so no outer background
          is needed — that would just produce a floating bar effect mid-panel. */}
      <div
        style={{
          position: "sticky",
          top: 0,
          "z-index": 1,
          "margin-bottom": "8px",
        }}
      >
        {/* Tab bar — categories of settings. Shows a count only while a search
            is active (how many matches live in each tab) and a discreet amber
            dot when that tab has dirty drafts. */}
        <div
          role="tablist"
          style={{
            display: "flex",
            gap: "4px",
            padding: "3px",
            background: "var(--c-shell)",
            border: "1px solid var(--c-edge)",
            "border-radius": "12px",
            "margin-bottom": "12px",
            "flex-wrap": "wrap",
          }}
        >
          <For each={visibleTabs()}>
            {(t) => {
              const c = () => countsByTab()[t.id];
              const active = () => tab() === t.id;
              return (
                <button
                  role="tab"
                  aria-selected={active()}
                  onClick={() => setActiveTab(t.id)}
                  title={t.hint}
                  class="btn btn--micro"
                  style={{
                    position: "relative",
                    background: active() ? "var(--c-raised)" : "transparent",
                    border: active() ? "1px solid var(--c-edge)" : "1px solid transparent",
                    color: active() ? "var(--c-bone)" : "var(--c-mist)",
                    display: "flex",
                    "align-items": "center",
                    flex: "1 1 auto",
                    "justify-content": "center",
                    "min-width": "0",
                  }}
                >
                  <span>{t.label}</span>
                  {/* Match count + dirty dot are absolutely positioned so they
                      never change the tab's flow size — no layout shift when a
                      search toggles the badge on and off. */}
                  <Show when={c().matches > 0}>
                    <span
                      class="f-mono"
                      style={{
                        position: "absolute",
                        top: "50%",
                        right: "5px",
                        transform: "translateY(-50%)",
                        "font-size": "9px",
                        color: "var(--c-amber)",
                        background: "rgba(212, 162, 89, 0.14)",
                        "border-radius": "999px",
                        padding: "1px 5px",
                        "pointer-events": "none",
                      }}
                      title={`${c().matches} search match${c().matches === 1 ? "" : "es"}`}
                    >
                      {c().matches}
                    </span>
                  </Show>
                  <Show when={c().dirty > 0}>
                    <span
                      style={{
                        position: "absolute",
                        top: "4px",
                        left: "5px",
                        width: "6px",
                        height: "6px",
                        "border-radius": "50%",
                        background: "var(--c-amber)",
                        "box-shadow": "0 0 4px var(--c-amber)",
                        "pointer-events": "none",
                      }}
                      title={`${c().dirty} unsaved draft${c().dirty === 1 ? "" : "s"}`}
                    />
                  </Show>
                </button>
              );
            }}
          </For>
        </div>

        <input
          type="text"
          class="field"
          style={{ width: "100%" }}
          placeholder="filter by key or description…"
          value={filter()}
          onInput={(e) => setFilter(e.currentTarget.value)}
        />
      </div>

      {/* Printer-only widgets — hidden during a global search so the results
          list isn't pushed down by tab-specific chrome. */}
      <Show when={tab() === "printer" && filter().trim() === ""}>
        <LetterheadCard />
        <ComposeFortuneCard />
      </Show>

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
                  <Widget row={row} />
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
            {filter().trim()
              ? `no settings matching "${filter()}"`
              : "no settings in this tab"}
          </div>
        </Show>
      </div>
    </Panel>
  );
};

void lsKeys;
