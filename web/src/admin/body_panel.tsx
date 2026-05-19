import { Component, createMemo, createSignal, onCleanup, onMount, For, Show } from "solid-js";
import { api } from "../shared/api";
import { EMOTION_COLORS, EMOTION_GLYPH, type Emotion } from "../shared/design";
import { lsGet, lsRemove, lsSet } from "../shared/local_storage";
import { NatsWs } from "../shared/nats_ws";
import { toast } from "../shared/toast";
import { Panel } from "./panel";

/**
 * Unified motion control. Combines the three things that all relate to
 * Lafufu's physical body into one panel:
 *
 *   - Expression pills (trigger preset emotion gestures)
 *   - Live pose + manual servo control (sliders that track animator.pose
 *     unless the operator is actively dragging)
 *
 * The standalone PoseView has been retired — the sliders' live thumbs
 * + numeric readouts give the same information.
 */

const EXPRESSIONS: Emotion[] = ["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"];

const RANGES: Array<{ name: string; lo: number; hi: number; glyph: string }> = [
  { name: "head_lr", lo: 1828, hi: 2298, glyph: "↔" },
  { name: "head_ud", lo: 2885, hi: 3278, glyph: "↕" },
  { name: "eye",     lo: 1960, hi: 2130, glyph: "◉" },
  { name: "jaw",     lo: 1534, hi: 1728, glyph: "▽" },
  { name: "brow",    lo: 2051, hi: 2099, glyph: "︿" },
];

const FACTORY_DEFAULTS: Record<string, number> = {
  head_lr: 2063, head_ud: 3082, eye: 2045, jaw: 1728, brow: 2075,
};

const DRAFT_KEY = "sliders/values";

export const BodyPanel: Component<{ nats: NatsWs }> = (props) => {
  // ─── Expressions ────────────────────────────────────────────────
  const [activeExpr, setActiveExpr] = createSignal<string | null>(null);

  const trigger = async (name: Emotion) => {
    setActiveExpr(name);
    try {
      await api.animatorExpression(name);
      toast.ok(`expression: ${name}`);
    } catch (e: any) {
      toast.err("expression failed", e.message);
    } finally {
      window.setTimeout(() => setActiveExpr((c) => (c === name ? null : c)), 600);
    }
  };

  // ─── Sliders / live pose ────────────────────────────────────────
  const [vals, setVals] = createSignal<Record<string, number>>({ ...FACTORY_DEFAULTS });
  const [live, setLive] = createSignal<Record<string, number>>({});
  const [savedVals, setSavedVals] = createSignal<Record<string, number>>({});
  const [saving, setSaving] = createSignal<string | null>(null);
  const [lastDragTs, setLastDragTs] = createSignal<Record<string, number>>({});

  let unsub: (() => void) | undefined;
  let pendingPreview: ReturnType<typeof setTimeout> | undefined;
  onMount(() => {
    const cached = lsGet<Record<string, number>>(DRAFT_KEY, {});
    if (Object.keys(cached).length > 0) {
      setVals((v) => ({ ...v, ...cached }));
      setSavedVals(cached);
    }
    unsub = props.nats.subscribe("animator.pose", (f) => setLive(f.payload));
  });
  onCleanup(() => {
    unsub?.();
    // Cancel any pending throttled preview so its closure doesn't fire after
    // the component is gone.
    if (pendingPreview) clearTimeout(pendingPreview);
  });

  // Effective slider value: user intent while actively dragging (last 800ms),
  // otherwise the live animator pose. Falls back to factory default if
  // we don't have a live reading yet.
  const effective = (name: string): number => {
    const dragAge = performance.now() - (lastDragTs()[name] ?? 0);
    if (dragAge < 800) return vals()[name] ?? FACTORY_DEFAULTS[name];
    return live()[name] ?? vals()[name] ?? FACTORY_DEFAULTS[name];
  };

  const onDrag = (name: string, position: number) => {
    setVals((v) => ({ ...v, [name]: position }));
    setLastDragTs((t) => ({ ...t, [name]: performance.now() }));
    if (pendingPreview) clearTimeout(pendingPreview);
    pendingPreview = setTimeout(() => api.animatorPreview(name, position).catch(() => {}), 40);
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
    setLastDragTs((t) => ({ ...t, [name]: performance.now() }));
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

  // ─── Render ─────────────────────────────────────────────────────
  return (
    <Panel
      title="Body"
      eyebrow="expressions · live pose · servo defaults"
      accent="var(--c-iris)"
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
      {/* ── Expression pills ───────────────────────────────────── */}
      <div
        style={{
          display: "grid",
          "grid-template-columns": "repeat(auto-fit, minmax(86px, 1fr))",
          gap: "8px",
          "margin-bottom": "20px",
        }}
      >
        <For each={EXPRESSIONS}>
          {(name) => {
            const color = EMOTION_COLORS[name];
            const isActive = () => activeExpr() === name;
            return (
              <button
                onClick={() => trigger(name)}
                title={`Play expression: ${name}`}
                style={{
                  display: "flex",
                  "flex-direction": "column",
                  "align-items": "center",
                  gap: "4px",
                  padding: "10px 6px",
                  "border-radius": "12px",
                  background: isActive()
                    ? `linear-gradient(155deg, ${color}33, ${color}11)`
                    : "rgba(243, 236, 220, 0.03)",
                  border: `1px solid ${isActive() ? color : "var(--c-edge)"}`,
                  color: "var(--c-bone)",
                  cursor: "pointer",
                  transition: "transform var(--t-fast), background var(--t-fast), border-color var(--t-fast)",
                  "min-width": "0",
                }}
                onMouseOver={(e) => { e.currentTarget.style.transform = "translateY(-1px)"; }}
                onMouseOut={(e) => { e.currentTarget.style.transform = "translateY(0)"; }}
              >
                <span
                  class="f-mono"
                  style={{
                    "font-size": "16px",
                    color,
                    "text-shadow": isActive() ? `0 0 12px ${color}` : "none",
                    transition: "text-shadow var(--t-fast)",
                  }}
                >
                  {EMOTION_GLYPH[name]}
                </span>
                <span
                  class="f-mono"
                  style={{
                    "font-size": "10px",
                    color: "var(--c-mist)",
                    "letter-spacing": ".04em",
                  }}
                >
                  {name}
                </span>
              </button>
            );
          }}
        </For>
      </div>

      {/* ── Divider with section label ─────────────────────────── */}
      <div
        style={{
          display: "flex",
          "align-items": "center",
          gap: "10px",
          "margin-bottom": "14px",
        }}
      >
        <div style={{ flex: 1, "border-top": "1px solid var(--c-edge)" }} />
        <span
          class="eyebrow"
          style={{ color: "var(--c-stone)", "letter-spacing": ".18em" }}
        >
          live pose · drag to set default
        </span>
        <div style={{ flex: 1, "border-top": "1px solid var(--c-edge)" }} />
      </div>

      {/* ── Sliders (also serve as live pose readout) ──────────── */}
      <div style={{ display: "flex", "flex-direction": "column", gap: "12px" }}>
        <For each={RANGES}>
          {({ name, lo, hi, glyph }) => {
            const v = () => effective(name);
            const frac = () => Math.max(0, Math.min(1, (v() - lo) / (hi - lo)));
            return (
              <div>
                <div
                  style={{
                    display: "flex",
                    "justify-content": "space-between",
                    "align-items": "center",
                    "margin-bottom": "4px",
                    gap: "8px",
                  }}
                >
                  <span
                    class="f-mono"
                    style={{
                      "font-size": "11px",
                      color: dirty(name) ? "var(--c-amber)" : "var(--c-mist)",
                      "letter-spacing": ".06em",
                      display: "flex",
                      "align-items": "center",
                      gap: "6px",
                    }}
                  >
                    <span style={{ color: "var(--c-stone)" }}>{glyph}</span>
                    {name}
                  </span>
                  <div style={{ display: "flex", "align-items": "center", gap: "6px" }}>
                    <span
                      class="f-mono f-num"
                      style={{
                        "font-size": "12px",
                        color: dirty(name) ? "var(--c-amber)" : "var(--c-bone)",
                        "min-width": "42px",
                        "text-align": "right",
                      }}
                    >
                      {v().toFixed(0)}
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
                <input
                  type="range"
                  class="slider"
                  style={{ width: "100%" }}
                  min={Math.min(lo, hi)} max={Math.max(lo, hi)}
                  value={v()}
                  onInput={(e) => onDrag(name, parseInt(e.currentTarget.value, 10))}
                />
                <div
                  style={{
                    display: "flex",
                    "justify-content": "space-between",
                    "margin-top": "2px",
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
