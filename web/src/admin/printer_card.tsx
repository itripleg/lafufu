import { Component, createSignal, For, onCleanup, onMount, Show } from "solid-js";
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

// Every card renders at this fixed size so nothing reflows as the selection
// changes. The scroll strip is exactly one card-row tall; further rows
// scroll into view.
const CARD_W = "168px";
const IMG_H = "224px";
const ROW_H = "264px";

const refOf = (a: PrinterAsset) => `${a.kind}/${a.name}`;
const displayName = (name: string) => name.replace(/\.(png|jpe?g|webp)$/i, "");
const kindLabel = (kind: string) => (kind === "default" ? "builtin" : kind);

/**
 * Letterhead + font gallery for the printer.
 *
 * There is always an active letterhead — the bundled white card is the
 * neutral fallback ("white" = plain text on white). The active card sits
 * pinned at the start of the row and never scrolls; every other letterhead
 * lives in a one-row-tall scroll strip beside it. Clicking a card opens a
 * preview modal with apply/cancel — selection commits only on apply, and
 * because asset URLs are stable the cards never reload.
 *
 * Fonts use the same defaults+uploads model; each chip previews itself in
 * its own typeface (loaded via the Font Loading API).
 */
export const LetterheadCard: Component = () => {
  const [letterheads, setLetterheads] = createSignal<PrinterAsset[]>([]);
  const [fonts, setFonts] = createSignal<PrinterAsset[]>([]);
  // Active selection tracked separately from the asset list so activating
  // something only flips a highlight — it never re-sets the list and so
  // never reloads the gallery images.
  const [activeRef, setActiveRef] = createSignal<string | null>(null);
  const [activeFontRef, setActiveFontRef] = createSignal<string | null>(null);
  const [busy, setBusy] = createSignal(false);
  // Bumped only on upload/delete (when the file set actually changes) so
  // cached cards survive activation untouched.
  const [version, setVersion] = createSignal(Date.now());
  // The card being previewed in the modal, or null (closed).
  const [modal, setModal] = createSignal<PrinterAsset | null>(null);

  let lhInput!: HTMLInputElement;
  let fontInput!: HTMLInputElement;
  const fontFamilies = new Set<string>();

  /** The pinned card — the active letterhead (always set once loaded). */
  const pinned = (): PrinterAsset | null =>
    letterheads().find((a) => refOf(a) === activeRef()) ?? null;

  /** Every letterhead that isn't pinned — these populate the scroll strip. */
  const others = (): PrinterAsset[] =>
    letterheads().filter((a) => refOf(a) !== activeRef());

  const reloadLetterheads = async () => {
    const lh = await api.listLetterheads();
    setLetterheads(lh.items);
    const active = lh.items.find((a) => a.active);
    setActiveRef(active ? refOf(active) : null);
  };

  const reloadFonts = async () => {
    const ft = await api.listFonts();
    setFonts(ft.items);
    const active = ft.items.find((a) => a.active);
    setActiveFontRef(active ? refOf(active) : null);
    ft.items.forEach(ensureFontFace);
  };

  onMount(() => {
    Promise.all([reloadLetterheads(), reloadFonts()]).catch((err) =>
      toast.err("could not load printer gallery", err.message),
    );
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModal(null);
    };
    window.addEventListener("keydown", onKey);
    onCleanup(() => window.removeEventListener("keydown", onKey));
  });

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
      await reloadLetterheads();
      setVersion(Date.now());
    } catch (err: any) {
      toast.err("upload failed", err.message);
    } finally {
      setBusy(false);
      if (lhInput) lhInput.value = "";
    }
  };

  /** Commit the modal's choice — the only place a selection is applied. */
  const applyModal = async () => {
    const m = modal();
    if (!m) return;
    setBusy(true);
    try {
      await api.activateLetterhead(m.kind, m.name);
      setActiveRef(refOf(m));
      toast.ok("letterhead active", m.name);
      setModal(null);
    } catch (err: any) {
      toast.err("could not apply letterhead", err.message);
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
      await reloadLetterheads();
      setVersion(Date.now());
    } catch (err: any) {
      toast.err("delete failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  /** The × on the *active* upload just deselects it (back to white) — it keeps
   *  the file in the gallery rather than deleting the letterhead in use. */
  const switchToWhite = async (e: Event) => {
    e.stopPropagation();
    setBusy(true);
    try {
      await api.activateLetterhead("default", "white.png");
      setActiveRef("default/white.png");
      toast.ok("letterhead set to white");
    } catch (err: any) {
      toast.err("could not switch to white", err.message);
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
      await reloadFonts();
    } catch (err: any) {
      toast.err("font upload failed", err.message);
    } finally {
      setBusy(false);
      if (fontInput) fontInput.value = "";
    }
  };

  const activateFont = async (a: PrinterAsset) => {
    if (refOf(a) === activeFontRef()) return;
    setBusy(true);
    try {
      await api.activateFont(a.kind, a.name);
      setActiveFontRef(refOf(a));
      toast.ok("compose font set", a.name);
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
      await reloadFonts();
    } catch (err: any) {
      toast.err("delete failed", err.message);
    } finally {
      setBusy(false);
    }
  };

  // ─── card tile ──────────────────────────────────────────
  const tileStyle = (active: boolean) => ({
    position: "relative" as const,
    width: CARD_W,
    padding: "8px",
    background: active ? "rgba(212, 162, 89, 0.12)" : "var(--c-shell)",
    border: `1px solid ${active ? "var(--c-amber)" : "var(--c-edge)"}`,
    "border-radius": "12px",
    cursor: "pointer",
    display: "flex",
    "flex-direction": "column" as const,
    "align-items": "center",
    gap: "6px",
    transition: "border-color var(--t-fast), background var(--t-fast)",
  });

  /** One letterhead card — used both pinned and inside the scroll strip. */
  const tile = (item: PrinterAsset, active: boolean) => (
    <button
      onClick={() => setModal(item)}
      disabled={busy()}
      title={`${kindLabel(item.kind)} · ${item.name}`}
      style={tileStyle(active)}
    >
      <div style={{ width: "100%", height: IMG_H, display: "flex", "align-items": "center", "justify-content": "center" }}>
        <img
          src={`${api.letterheadFileUrl(item.kind, item.name)}?v=${version()}`}
          alt={item.name}
          style={{ "max-width": "100%", "max-height": "100%", "object-fit": "contain", "border-radius": "4px", background: "var(--c-shell)" }}
        />
      </div>
      <span
        class="f-mono"
        style={{
          "font-size": "10px",
          color: active ? "var(--c-amber)" : "var(--c-mist)",
          "max-width": "100%",
          overflow: "hidden",
          "text-overflow": "ellipsis",
          "white-space": "nowrap",
        }}
      >
        {displayName(item.name)}
      </span>
      <Show when={active}>
        <span class="f-mono" style={{
          position: "absolute", top: "10px", left: "10px",
          "font-size": "8px", color: "var(--c-amber)",
          background: "rgba(20,16,12,.82)", padding: "1px 5px", "border-radius": "999px",
        }}>
          active
        </span>
      </Show>
      <Show when={item.kind === "default" && !active}>
        <span class="f-mono" style={{
          position: "absolute", top: "10px", left: "10px",
          "font-size": "8px", color: "var(--c-stone)",
          background: "rgba(20,16,12,.7)", padding: "1px 5px", "border-radius": "999px",
        }}>
          builtin
        </span>
      </Show>
      <Show when={item.kind === "upload"}>
        <span role="button"
          title={active ? "switch to white (keeps the upload)" : "delete upload"}
          onClick={(e) => (active ? switchToWhite(e) : deleteLetterhead(item, e))}
          style={{
            position: "absolute", top: "8px", right: "8px",
            width: "20px", height: "20px", "border-radius": "50%",
            background: "rgba(20,16,12,.85)", border: "1px solid var(--c-edge)",
            color: active ? "var(--c-mist)" : "var(--c-coral)",
            "font-size": "12px", "line-height": "18px", "text-align": "center",
          }}>
          ×
        </span>
      </Show>
    </button>
  );

  // ─── render ─────────────────────────────────────────────
  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", "align-items": "center", "margin-bottom": "12px", gap: "10px", "flex-wrap": "wrap" }}>
        {tag("printer letterhead")}
        {eyebrow("gallery · white = plain background")}
        <div style={{ flex: 1 }} />
        <button class="btn btn--ghost btn--micro" onClick={() => lhInput?.click()} disabled={busy()}>
          upload image
        </button>
        <button class="btn btn--ghost btn--micro" onClick={testPrint} disabled={busy()}
          title="Print a half-inch grid + corner markers to dial in offsets">
          test print
        </button>
        <button class="btn btn--primary btn--micro" onClick={printActive}
          disabled={busy() || !pinned()} title="Print the active letterhead">
          print
        </button>
        <input ref={lhInput} type="file" accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }} onChange={onLetterheadFile} />
      </div>

      {/* Pinned active card (left) — never scrolls — beside a one-row-tall
          scroll strip holding every other card. */}
      <div style={{ display: "flex", gap: "12px", "align-items": "flex-start" }}>
        <div style={{ "flex-shrink": 0 }}>
          <Show
            when={pinned()}
            fallback={<div style={{ width: CARD_W, height: ROW_H }} />}
          >
            {(p) => tile(p(), true)}
          </Show>
        </div>
        <div style={{ width: "1px", height: ROW_H, background: "var(--c-edge)", "flex-shrink": 0 }} />
        <div
          class="scroll-warm"
          style={{
            flex: 1,
            "min-width": 0,
            height: ROW_H,
            "overflow-y": "auto",
            display: "grid",
            "grid-template-columns": `repeat(auto-fill, ${CARD_W})`,
            "align-content": "start",
            gap: "12px",
          }}
        >
          <For
            each={others()}
            fallback={
              <span style={{ color: "var(--c-stone)", "font-style": "italic", "font-size": "13px" }}>
                no other letterheads
              </span>
            }
          >
            {(item) => tile(item, false)}
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
            {(a) => {
              const selected = () => activeFontRef() === refOf(a);
              return (
                <button
                  onClick={() => activateFont(a)}
                  disabled={busy()}
                  title={`${kindLabel(a.kind)} · ${a.name}`}
                  style={{
                    position: "relative",
                    padding: "8px 14px",
                    background: selected() ? "rgba(212, 162, 89, 0.1)" : "var(--c-shell)",
                    border: `1px solid ${selected() ? "var(--c-amber)" : "var(--c-edge)"}`,
                    "border-radius": "10px",
                    cursor: "pointer",
                    color: selected() ? "var(--c-bone)" : "var(--c-mist)",
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
              );
            }}
          </For>
        </div>
      </div>

      <div style={{ "font-size": "12px", "line-height": 1.4, color: "var(--c-stone)", "margin-top": "12px", "font-style": "italic" }}>
        The active letterhead is printed behind each reply — leave the middle
        band blank for the text overlay, or pick <strong>white</strong> for a
        plain background. Compose draws body text in the active font.
        Images ≤10 MB (PNG/JPEG/WebP); fonts ≤5 MB (TTF/OTF).
      </div>

      {/* Preview modal — selection only commits on apply. */}
      <Show when={modal()}>
        {(m) => (
          <div
            onClick={() => setModal(null)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0, 0, 0, 0.72)",
              display: "flex",
              "align-items": "center",
              "justify-content": "center",
              "z-index": 900,
              padding: "24px",
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                background: "var(--c-raised)",
                border: "1px solid var(--c-edge)",
                "border-radius": "16px",
                padding: "20px",
                "max-width": "460px",
                width: "100%",
                display: "flex",
                "flex-direction": "column",
                "align-items": "center",
                gap: "14px",
              }}
            >
              <img
                src={`${api.letterheadFileUrl(m().kind, m().name)}?v=${version()}`}
                alt={m().name}
                style={{ "max-width": "100%", "max-height": "64vh", "border-radius": "8px", background: "var(--c-shell)" }}
              />
              <span class="f-mono" style={{ "font-size": "11px", color: "var(--c-mist)" }}>
                {kindLabel(m().kind)} · {m().name}
              </span>
              <div style={{ display: "flex", gap: "10px", "align-self": "stretch", "justify-content": "flex-end" }}>
                <button class="btn btn--ghost btn--tiny" onClick={() => setModal(null)} disabled={busy()}>
                  cancel
                </button>
                <button class="btn btn--primary btn--tiny" onClick={applyModal} disabled={busy()}>
                  {busy() ? "…" : "apply"}
                </button>
              </div>
            </div>
          </div>
        )}
      </Show>
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
