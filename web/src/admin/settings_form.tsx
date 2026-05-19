import { Component, createSignal, onMount, For, Show, createMemo, onCleanup } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
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
};

const DYNAMIC_OPTIONS: Record<string, () => Promise<string[]>> = {
  "agent.llm_model": async () => {
    const { models } = await api.listLlmModels();
    return models.map((m) => m.name);
  },
  "agent.stt_backend": async () => {
    const { backends } = await api.listSttBackends();
    return backends.filter((b) => b.available).map((b) => b.id);
  },
};

const DRAFT_PREFIX = "settings/draft/";

/** Image-upload widget for the printer letterhead. Shown in the Printer tab. */
const LetterheadCard: Component = () => {
  // Bumped on every successful upload/delete to bust the <img> cache.
  const [version, setVersion] = createSignal(Date.now());
  const [busy, setBusy] = createSignal(false);
  const [hasImage, setHasImage] = createSignal(true);  // optimistic; <img onerror> flips it
  let fileInput!: HTMLInputElement;

  const pick = () => fileInput?.click();

  const onFile = async (e: Event) => {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      await api.uploadLetterhead(f);
      setHasImage(true);
      setVersion(Date.now());
      toast.ok(`letterhead uploaded`, `${f.name} · ${(f.size / 1024).toFixed(1)} KB`);
    } catch (err: any) {
      toast.err("upload failed", err.message);
    } finally {
      setBusy(false);
      if (fileInput) fileInput.value = "";
    }
  };

  const remove = async () => {
    if (!window.confirm("Remove uploaded letterhead?")) return;
    setBusy(true);
    try {
      await api.deleteLetterhead();
      setHasImage(false);
      setVersion(Date.now());
      toast.ok("letterhead removed");
    } catch (err: any) {
      toast.err("remove failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        padding: "14px",
        "border-radius": "14px",
        background: "rgba(243, 236, 220, 0.02)",
        border: "1px solid var(--c-edge)",
        "margin-bottom": "16px",
      }}
    >
      <div style={{ display: "flex", "align-items": "center", "margin-bottom": "10px", gap: "10px" }}>
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
          printer.letterhead
        </code>
        <span
          class="f-mono"
          style={{ "font-size": "10px", color: "var(--c-stone)", "letter-spacing": ".08em", "text-transform": "uppercase" }}
        >
          image
        </span>
        <div style={{ flex: 1 }} />
        <button class="btn btn--ghost btn--micro" onClick={pick} disabled={busy()}>
          {busy() ? "…" : hasImage() ? "replace" : "upload"}
        </button>
        <button
          class="btn btn--ghost btn--micro"
          onClick={async () => {
            setBusy(true);
            try {
              await api.testPrint();
              toast.ok("calibration print sent", "measure offsets against the half-inch grid");
            } catch (err: any) {
              toast.err("test print failed", err.message);
            } finally {
              setBusy(false);
            }
          }}
          disabled={busy()}
          title="Print a half-inch grid + corner markers to dial in offsets"
        >
          test print
        </button>
        <Show when={hasImage()}>
          <button
            class="btn btn--primary btn--micro"
            onClick={async () => {
              setBusy(true);
              try {
                await api.printLetterhead();
                toast.ok("print sent", "queued on the default printer");
              } catch (err: any) {
                toast.err("print failed", err.message);
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy()}
            title="Send the letterhead image to the printer"
          >
            print
          </button>
          <button class="btn btn--ghost btn--micro" onClick={remove} disabled={busy()}>
            remove
          </button>
        </Show>
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }}
          onChange={onFile}
        />
      </div>

      <Show
        when={hasImage()}
        fallback={
          <div
            style={{
              padding: "32px 12px",
              "text-align": "center",
              color: "var(--c-stone)",
              "font-style": "italic",
              "font-family": "var(--f-display)",
              "font-size": "14px",
            }}
          >
            no letterhead uploaded — replies will print as plain text
          </div>
        }
      >
        <div
          style={{
            display: "flex",
            "justify-content": "center",
            background: "var(--c-shell)",
            "border-radius": "10px",
            padding: "10px",
            border: "1px solid var(--c-edge)",
          }}
        >
          <img
            src={`${api.letterheadUrl()}?v=${version()}`}
            alt="printer letterhead"
            style={{ "max-width": "100%", "max-height": "260px", "border-radius": "4px" }}
            onError={() => setHasImage(false)}
            onLoad={() => setHasImage(true)}
          />
        </div>
      </Show>

      <div
        style={{
          "font-size": "12px",
          "line-height": 1.4,
          color: "var(--c-stone)",
          "margin-top": "10px",
          "font-style": "italic",
        }}
      >
        Image printed behind each reply — leave the middle area blank for the
        text overlay. Max 10 MB, PNG/JPEG/WebP.
      </div>
    </div>
  );
};

/** Manual fortune composer — type text + optional lucky info, server overlays
 *  it on the uploaded letterhead and prints. Useful before the system prompt
 *  is tuned for fortune-style replies. */
const ComposeFortuneCard: Component = () => {
  const DEFAULT_TEXT =
    "I have watched you pass beneath the city lights, carrying questions you have not yet admitted are questions.\n\n" +
    "Tonight, New York will answer in fragments: a reflection in a train window, a stranger's sentence, a door left open, a sign flickering at the exact wrong moment.\n\n" +
    "Do not look for one grand revelation. Look for three small glitches in the ordinary world.";

  const [text, setText] = createSignal(lsGet<string>("compose/text", DEFAULT_TEXT));
  const [stop, setStop] = createSignal(lsGet<string>("compose/stop", "Canal Street"));
  const [nums, setNums] = createSignal(lsGet<string>("compose/nums", "5, 10, 20"));
  const [sending, setSending] = createSignal(false);

  const onText = (v: string) => { setText(v); lsSet("compose/text", v); };
  const onStop = (v: string) => { setStop(v); lsSet("compose/stop", v); };
  const onNums = (v: string) => { setNums(v); lsSet("compose/nums", v); };

  const parseNums = (s: string): number[] | undefined => {
    const arr = s.split(/[,\s]+/).map((x) => parseInt(x.trim(), 10)).filter((n) => !Number.isNaN(n));
    return arr.length ? arr : undefined;
  };

  const submit = async () => {
    const body = text().trim();
    if (!body) { toast.warn("nothing to print", "type some fortune text first"); return; }
    setSending(true);
    try {
      await api.composePrint({
        text: body,
        lucky_subway_stop: stop().trim() || undefined,
        lucky_numbers: parseNums(nums()),
      });
      toast.ok("composed fortune sent", "PIL overlay → printer queue");
    } catch (err: any) {
      toast.err("compose failed", err.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      style={{
        padding: "14px",
        "border-radius": "14px",
        background: "rgba(243, 236, 220, 0.02)",
        border: "1px solid var(--c-edge)",
        "margin-bottom": "16px",
      }}
    >
      <div
        style={{
          display: "flex", "align-items": "center", "margin-bottom": "10px", gap: "10px",
        }}
      >
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
          compose fortune
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
          text → letterhead overlay
        </span>
        <div style={{ flex: 1 }} />
        <button
          class="btn btn--primary btn--micro"
          disabled={sending() || !text().trim()}
          onClick={submit}
          title="Compose this text onto the letterhead and print it"
        >
          {sending() ? "…" : "compose & print"}
        </button>
      </div>

      <textarea
        class="field"
        rows="6"
        style={{
          width: "100%",
          "font-family": "var(--f-sans)",
          resize: "vertical",
          "margin-bottom": "10px",
        }}
        placeholder="The fortune body — paragraphs separated by blank lines wrap nicely…"
        value={text()}
        onInput={(e) => onText(e.currentTarget.value)}
      />

      <div style={{ display: "flex", gap: "8px", "flex-wrap": "wrap" }}>
        <label
          class="f-mono"
          style={{
            "font-size": "11px",
            color: "var(--c-stone)",
            display: "flex", "flex-direction": "column", gap: "4px",
            flex: "1 1 220px",
          }}
        >
          lucky subway stop
          <input
            type="text"
            class="field"
            style={{ "font-family": "var(--f-sans)" }}
            value={stop()}
            onInput={(e) => onStop(e.currentTarget.value)}
            placeholder="(optional)"
          />
        </label>
        <label
          class="f-mono"
          style={{
            "font-size": "11px",
            color: "var(--c-stone)",
            display: "flex", "flex-direction": "column", gap: "4px",
            flex: "1 1 160px",
          }}
        >
          lucky numbers
          <input
            type="text"
            class="field f-num"
            style={{ "font-family": "var(--f-mono)" }}
            value={nums()}
            onInput={(e) => onNums(e.currentTarget.value)}
            placeholder="5, 10, 20"
          />
        </label>
      </div>

      <div
        style={{
          "font-size": "12px",
          "line-height": 1.4,
          color: "var(--c-stone)",
          "margin-top": "10px",
          "font-style": "italic",
        }}
      >
        Server-side PIL composition: body text auto-shrinks to fit the empty
        middle band of the card; lucky info sits in a smaller line near the
        bottom. Font: IM Fell English (bundled, 17th-c. revival serif).
      </div>
    </div>
  );
};

type Tab = "audio" | "model" | "printer" | "other";

const TABS: Array<{ id: Tab; label: string; hint: string }> = [
  { id: "audio",   label: "audio",   hint: "speaker, tts, mic" },
  { id: "model",   label: "model",   hint: "llm + system prompt" },
  { id: "printer", label: "printer", hint: "auto-print + letterhead" },
  { id: "other",   label: "other",   hint: "animator, misc" },
];

function categoryOf(key: string): Tab {
  if (key === "agent.llm_model" || key === "agent.system_prompt") return "model";
  if (key.startsWith("printer.")) return "printer";
  if (
    key.startsWith("speaker.") ||
    key.startsWith("tts.") ||
    key === "agent.silence_threshold" ||
    key === "agent.silence_seconds" ||
    key === "agent.auto_listen" ||
    key === "agent.stt_backend" ||
    key === "agent.whisper_model"
  ) return "audio";
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
  const [dynamicOptions, setDynamicOptions] = createSignal<Record<string, string[]>>({});
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
      // type="text" + inputMode keeps partial input like "1." from being
      // wiped by browser number normalization while still showing the right
      // virtual keyboard on mobile. Validation happens at commit (parseValue).
      const inputMode = row.value_type === "float" ? "decimal" : "numeric";
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

  // Per-tab counts so the tab pills can show how many keys live in each
  // category — and the user can see at a glance which tab has dirty drafts.
  const countsByTab = createMemo(() => {
    const out: Record<Tab, { total: number; dirty: number }> = {
      audio: { total: 0, dirty: 0 },
      model: { total: 0, dirty: 0 },
      printer: { total: 0, dirty: 0 },
      other: { total: 0, dirty: 0 },
    };
    for (const r of rows) {
      const c = categoryOf(r.key);
      out[c].total++;
      if (isDirty(r)) out[c].dirty++;
    }
    return out;
  });

  const filtered = createMemo(() => {
    const q = filter().trim().toLowerCase();
    const tabbed = rows.filter((r) => categoryOf(r.key) === tab());
    if (!q) return tabbed;
    return tabbed.filter((r) =>
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
      {/* Tab bar — categories of settings, with per-tab key counts and a
          discreet amber dot when that tab has dirty drafts. */}
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
        <For each={TABS}>
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
                  background: active() ? "var(--c-raised)" : "transparent",
                  border: active() ? "1px solid var(--c-edge)" : "1px solid transparent",
                  color: active() ? "var(--c-bone)" : "var(--c-mist)",
                  display: "flex",
                  "align-items": "center",
                  gap: "6px",
                  flex: "1 1 auto",
                  "justify-content": "center",
                  "min-width": "0",
                }}
              >
                <span>{t.label}</span>
                <span
                  class="f-mono"
                  style={{ "font-size": "10px", color: "var(--c-stone)" }}
                >
                  {c().total}
                </span>
                <Show when={c().dirty > 0}>
                  <span
                    style={{
                      width: "6px",
                      height: "6px",
                      "border-radius": "50%",
                      background: "var(--c-amber)",
                      "box-shadow": "0 0 4px var(--c-amber)",
                    }}
                    title={`${c().dirty} unsaved draft${c().dirty === 1 ? "" : "s"}`}
                  />
                </Show>
              </button>
            );
          }}
        </For>
      </div>

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

      <Show when={tab() === "printer"}>
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
