import { Component, createMemo, createSignal, For, onMount, Show } from "solid-js";
import { api, type PrinterAsset } from "../shared/api";
import { lsGet, lsSet } from "../shared/local_storage";
import { toast } from "../shared/toast";

/** Shared card chrome for the printer widgets. */
const cardStyle = {
  padding: "14px",
  "border-radius": "14px",
  background: "rgba(243, 236, 220, 0.02)",
  border: "1px solid var(--c-edge)",
  "margin-bottom": "16px",
} as const;

const tag = (text: string) => (
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
    {text}
  </code>
);

const eyebrow = (text: string) => (
  <span
    class="f-mono"
    style={{
      "font-size": "10px",
      color: "var(--c-stone)",
      "letter-spacing": ".08em",
      "text-transform": "uppercase",
    }}
  >
    {text}
  </span>
);

/**
 * Letterhead + font gallery for the printer.
 *
 * Letterheads: bundled defaults + operator uploads, shown as a thumbnail
 * gallery beside a large preview of the active one. Click any thumbnail to
 * activate it; uploads can be deleted.
 *
 * Fonts: the same model for the typeface compose draws with. Each font chip
 * renders its own name in its own typeface (loaded via the Font Loading API).
 */
export const LetterheadCard: Component = () => {
  const [letterheads, setLetterheads] = createSignal<PrinterAsset[]>([]);
  const [fonts, setFonts] = createSignal<PrinterAsset[]>([]);
  const [busy, setBusy] = createSignal(false);
  // Bumped after any mutation to bust cached <img> / active-preview URLs.
  const [version, setVersion] = createSignal(Date.now());

  let lhInput!: HTMLInputElement;
  let fontInput!: HTMLInputElement;
  const fontFamilies = new Set<string>();

  const activeLetterhead = createMemo(() => letterheads().find((a) => a.active));

  const reload = async () => {
    try {
      const [lh, ft] = await Promise.all([api.listLetterheads(), api.listFonts()]);
      setLetterheads(lh.items);
      setFonts(ft.items);
      setVersion(Date.now());
      ft.items.forEach(ensureFontFace);
    } catch (err: any) {
      toast.err("could not load printer gallery", err.message);
    }
  };

  onMount(reload);

  /** Register a @font-face for a font asset so its chip can preview itself. */
  const ensureFontFace = (a: PrinterAsset): string => {
    const family = `lafufu-pf-${a.kind}-${a.name}`.replace(/[^a-zA-Z0-9-]/g, "-");
    if (!fontFamilies.has(family)) {
      fontFamilies.add(family);
      const ff = new FontFace(family, `url("${api.fontFileUrl(a.kind, a.name)}")`);
      ff.load()
        .then((loaded) => document.fonts.add(loaded))
        .catch(() => {/* preview just falls back to the default serif */});
    }
    return family;
  };

  // ─── letterhead actions ─────────────────────────────────
  const onLetterheadFile = async (e: Event) => {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      await api.uploadLetterhead(f);
      toast.ok("letterhead uploaded", `${f.name} · ${(f.size / 1024).toFixed(1)} KB`);
      await reload();
    } catch (err: any) {
      toast.err("upload failed", err.message);
    } finally {
      setBusy(false);
      if (lhInput) lhInput.value = "";
    }
  };

  const activateLetterhead = async (a: PrinterAsset) => {
    if (a.active) return;
    setBusy(true);
    try {
      await api.activateLetterhead(a.kind, a.name);
      toast.ok("letterhead active", a.name);
      await reload();
    } catch (err: any) {
      toast.err("could not activate", err.message);
    } finally {
      setBusy(false);
    }
  };

  const deleteLetterhead = async (a: PrinterAsset, e: Event) => {
    e.stopPropagation();
    if (!window.confirm(`Delete uploaded letterhead "${a.name}"?`)) return;
    setBusy(true);
    try {
      await api.deleteLetterheadFile(a.kind, a.name);
      toast.ok("letterhead deleted", a.name);
      await reload();
    } catch (err: any) {
      toast.err("delete failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  const printActive = async () => {
    setBusy(true);
    try {
      await api.printLetterhead();
      toast.ok("print sent", "queued on the default printer");
    } catch (err: any) {
      toast.err("print failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  const testPrint = async () => {
    setBusy(true);
    try {
      await api.testPrint();
      toast.ok("calibration print sent", "measure offsets against the half-inch grid");
    } catch (err: any) {
      toast.err("test print failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  // ─── font actions ───────────────────────────────────────
  const onFontFile = async (e: Event) => {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      await api.uploadFont(f);
      toast.ok("font uploaded", f.name);
      await reload();
    } catch (err: any) {
      toast.err("font upload failed", err.message);
    } finally {
      setBusy(false);
      if (fontInput) fontInput.value = "";
    }
  };

  const activateFont = async (a: PrinterAsset) => {
    if (a.active) return;
    setBusy(true);
    try {
      await api.activateFont(a.kind, a.name);
      toast.ok("compose font set", a.name);
      await reload();
    } catch (err: any) {
      toast.err("could not set font", err.message);
    } finally {
      setBusy(false);
    }
  };

  const deleteFont = async (a: PrinterAsset, e: Event) => {
    e.stopPropagation();
    if (!window.confirm(`Delete uploaded font "${a.name}"?`)) return;
    setBusy(true);
    try {
      await api.deleteFont(a.kind, a.name);
      toast.ok("font deleted", a.name);
      await reload();
    } catch (err: any) {
      toast.err("delete failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  // ─── render ─────────────────────────────────────────────
  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", "align-items": "center", "margin-bottom": "12px", gap: "10px", "flex-wrap": "wrap" }}>
        {tag("printer letterhead")}
        {eyebrow("gallery · defaults + uploads")}
        <div style={{ flex: 1 }} />
        <button class="btn btn--ghost btn--micro" onClick={() => lhInput?.click()} disabled={busy()}>
          upload image
        </button>
        <button class="btn btn--ghost btn--micro" onClick={testPrint} disabled={busy()}
          title="Print a half-inch grid + corner markers to dial in offsets">
          test print
        </button>
        <button class="btn btn--primary btn--micro" onClick={printActive}
          disabled={busy() || !activeLetterhead()} title="Print the active letterhead">
          print
        </button>
        <input ref={lhInput} type="file" accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }} onChange={onLetterheadFile} />
      </div>

      {/* Active preview (left) beside the gallery (right). */}
      <div
        style={{
          display: "grid",
          "grid-template-columns": "minmax(150px, 240px) 1fr",
          gap: "14px",
          "align-items": "start",
        }}
      >
        {/* Active preview */}
        <div
          style={{
            display: "flex",
            "flex-direction": "column",
            "align-items": "center",
            "justify-content": "center",
            "min-height": "220px",
            background: "var(--c-shell)",
            border: "1px solid var(--c-edge)",
            "border-radius": "12px",
            padding: "12px",
          }}
        >
          <Show
            when={activeLetterhead()}
            fallback={
              <span style={{ color: "var(--c-stone)", "font-style": "italic", "font-size": "13px", "text-align": "center" }}>
                no letterhead active — pick one from the gallery →
              </span>
            }
          >
            <img
              src={`${api.letterheadUrl()}?v=${version()}`}
              alt="active letterhead"
              style={{ "max-width": "100%", "max-height": "280px", "border-radius": "6px" }}
            />
            <span class="f-mono" style={{ "font-size": "10px", color: "var(--c-amber)", "margin-top": "8px" }}>
               active · {activeLetterhead()!.name}
            </span>
          </Show>
        </div>

        {/* Gallery */}
        <div
          style={{
            display: "grid",
            "grid-template-columns": "repeat(auto-fill, minmax(96px, 1fr))",
            gap: "8px",
          }}
        >
          <For
            each={letterheads()}
            fallback={
              <span style={{ color: "var(--c-stone)", "font-style": "italic", "font-size": "13px" }}>
                no letterheads found
              </span>
            }
          >
            {(a) => (
              <button
                onClick={() => activateLetterhead(a)}
                disabled={busy()}
                title={`${a.kind} · ${a.name}`}
                style={{
                  position: "relative",
                  padding: "6px",
                  background: a.active ? "rgba(212, 162, 89, 0.1)" : "var(--c-shell)",
                  border: `1px solid ${a.active ? "var(--c-amber)" : "var(--c-edge)"}`,
                  "border-radius": "10px",
                  cursor: "pointer",
                  display: "flex",
                  "flex-direction": "column",
                  "align-items": "center",
                  gap: "4px",
                  transition: "border-color var(--t-fast), background var(--t-fast)",
                }}
              >
                <img
                  src={`${api.letterheadFileUrl(a.kind, a.name)}?v=${version()}`}
                  alt={a.name}
                  style={{ width: "100%", height: "84px", "object-fit": "contain", "border-radius": "4px" }}
                />
                <span
                  class="f-mono"
                  style={{
                    "font-size": "9px",
                    color: a.active ? "var(--c-amber)" : "var(--c-mist)",
                    "max-width": "100%",
                    overflow: "hidden",
                    "text-overflow": "ellipsis",
                    "white-space": "nowrap",
                  }}
                >
                  {a.name}
                </span>
                <Show when={a.kind === "upload"}>
                  <span
                    role="button"
                    title="delete upload"
                    onClick={(e) => deleteLetterhead(a, e)}
                    style={{
                      position: "absolute",
                      top: "3px",
                      right: "3px",
                      width: "18px",
                      height: "18px",
                      "border-radius": "50%",
                      background: "rgba(20,16,12,.8)",
                      border: "1px solid var(--c-edge)",
                      color: "var(--c-coral)",
                      "font-size": "11px",
                      "line-height": "16px",
                      "text-align": "center",
                    }}
                  >
                    ×
                  </span>
                </Show>
                <Show when={a.kind === "default"}>
                  <span
                    class="f-mono"
                    style={{ position: "absolute", top: "5px", left: "5px", "font-size": "8px", color: "var(--c-stone)" }}
                  >
                    default
                  </span>
                </Show>
              </button>
            )}
          </For>
        </div>
      </div>

      {/* Font picker */}
      <div style={{ "margin-top": "16px", "border-top": "1px solid var(--c-edge)", "padding-top": "14px" }}>
        <div style={{ display: "flex", "align-items": "center", gap: "10px", "margin-bottom": "10px", "flex-wrap": "wrap" }}>
          {tag("compose font")}
          {eyebrow("text drawn onto the letterhead")}
          <div style={{ flex: 1 }} />
          <button class="btn btn--ghost btn--micro" onClick={() => fontInput?.click()} disabled={busy()}>
            upload font
          </button>
          <input ref={fontInput} type="file" accept=".ttf,.otf,font/ttf,font/otf"
            style={{ display: "none" }} onChange={onFontFile} />
        </div>
        <div style={{ display: "flex", gap: "8px", "flex-wrap": "wrap" }}>
          <For
            each={fonts()}
            fallback={
              <span style={{ color: "var(--c-stone)", "font-style": "italic", "font-size": "13px" }}>
                no fonts found
              </span>
            }
          >
            {(a) => (
              <button
                onClick={() => activateFont(a)}
                disabled={busy()}
                title={`${a.kind} · ${a.name}`}
                style={{
                  position: "relative",
                  padding: "8px 14px",
                  background: a.active ? "rgba(212, 162, 89, 0.1)" : "var(--c-shell)",
                  border: `1px solid ${a.active ? "var(--c-amber)" : "var(--c-edge)"}`,
                  "border-radius": "10px",
                  cursor: "pointer",
                  color: a.active ? "var(--c-bone)" : "var(--c-mist)",
                  "font-family": `'${ensureFontFace(a)}', var(--f-display, serif)`,
                  "font-size": "16px",
                  display: "flex",
                  "align-items": "center",
                  gap: "8px",
                  transition: "border-color var(--t-fast), background var(--t-fast)",
                }}
              >
                {a.name.replace(/\.(ttf|otf)$/i, "")}
                <Show when={a.kind === "upload"}>
                  <span
                    role="button"
                    title="delete font"
                    onClick={(e) => deleteFont(a, e)}
                    style={{ color: "var(--c-coral)", "font-size": "13px" }}
                  >
                    ×
                  </span>
                </Show>
              </button>
            )}
          </For>
        </div>
      </div>

      <div style={{ "font-size": "12px", "line-height": 1.4, color: "var(--c-stone)", "margin-top": "12px", "font-style": "italic" }}>
        The active letterhead is printed behind each reply — leave the middle
        band blank for the text overlay. Compose draws body text in the active
        font. Images ≤10 MB (PNG/JPEG/WebP); fonts ≤5 MB (TTF/OTF).
      </div>
    </div>
  );
};

/** Manual fortune composer — type text + optional lucky info; the server
 *  overlays it on the active letterhead with the active font and prints. */
export const ComposeFortuneCard: Component = () => {
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
    <div style={cardStyle}>
      <div style={{ display: "flex", "align-items": "center", "margin-bottom": "10px", gap: "10px" }}>
        {tag("compose fortune")}
        {eyebrow("text → letterhead overlay")}
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
        style={{ width: "100%", "font-family": "var(--f-sans)", resize: "vertical", "margin-bottom": "10px" }}
        placeholder="The fortune body — paragraphs separated by blank lines wrap nicely…"
        value={text()}
        onInput={(e) => onText(e.currentTarget.value)}
      />

      <div style={{ display: "flex", gap: "8px", "flex-wrap": "wrap" }}>
        <label
          class="f-mono"
          style={{ "font-size": "11px", color: "var(--c-stone)", display: "flex", "flex-direction": "column", gap: "4px", flex: "1 1 220px" }}
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
          style={{ "font-size": "11px", color: "var(--c-stone)", display: "flex", "flex-direction": "column", gap: "4px", flex: "1 1 160px" }}
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

      <div style={{ "font-size": "12px", "line-height": 1.4, color: "var(--c-stone)", "margin-top": "10px", "font-style": "italic" }}>
        Server-side PIL composition: body text auto-shrinks to fit the empty
        middle band of the card; lucky info sits in a smaller line near the
        bottom. Uses the font selected in the letterhead panel above.
      </div>
    </div>
  );
};
