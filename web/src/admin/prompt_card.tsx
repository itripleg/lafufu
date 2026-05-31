import { Component, createMemo, createSignal, For, onMount, Show } from "solid-js";
import { api, type PromptsState } from "../shared/api";
import { toast } from "../shared/toast";

/** Shared card chrome — kept module-private here (mirrors printer_card.tsx). */
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
 * Prompt switcher for the agent tab.
 *
 * Two built-in presets (Street Oracle / Fortune Teller). The active preset's
 * text is the live `agent.system_prompt` the agent consumes; selecting,
 * editing, or restoring the active preset mirrors into it and the agent
 * live-reloads. This card owns those raw keys, so settings_form hides them
 * from the generic list.
 */
export const PromptCard: Component = () => {
  const [state, setState] = createSignal<PromptsState | null>(null);
  // The textarea's working copy, seeded from the active preset's saved text.
  const [draft, setDraft] = createSignal("");
  const [busy, setBusy] = createSignal(false);

  const activePreset = createMemo(() => {
    const s = state();
    if (!s) return null;
    return s.presets.find((p) => p.id === s.active) ?? s.presets[0] ?? null;
  });

  const dirty = () => {
    const a = activePreset();
    return a != null && draft() !== a.text;
  };

  /** Adopt a fresh server state + reseed the textarea from the active preset. */
  const adopt = (s: PromptsState) => {
    setState(s);
    const active = s.presets.find((p) => p.id === s.active) ?? s.presets[0];
    setDraft(active ? active.text : "");
  };

  onMount(async () => {
    try {
      adopt(await api.getPrompts());
    } catch (err: any) {
      toast.err("could not load prompts", err?.message ?? String(err));
    }
  });

  const select = async (id: string) => {
    const s = state();
    if (busy() || !s || id === s.active) return;
    setBusy(true);
    try {
      adopt(await api.selectPrompt(id));
      const label = state()?.presets.find((p) => p.id === id)?.label ?? id;
      toast.ok("prompt switched", label);
    } catch (err: any) {
      toast.err("could not switch prompt", err?.message ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const a = activePreset();
    if (busy() || !a || !dirty()) return;
    setBusy(true);
    try {
      adopt(await api.savePrompt(a.id, draft()));
      toast.ok("prompt saved", a.label);
    } catch (err: any) {
      toast.err("could not save prompt", err?.message ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    const a = activePreset();
    if (busy() || !a) return;
    if (!window.confirm(`Restore "${a.label}" to its shipped default text? This discards your edits.`)) return;
    setBusy(true);
    try {
      adopt(await api.restorePrompt(a.id));
      toast.ok("prompt restored to default", a.label);
    } catch (err: any) {
      toast.err("could not restore prompt", err?.message ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  // Restore is meaningful only when there's something to undo: the active
  // preset has been edited away from its shipped text, or there are unsaved
  // local edits in the textarea.
  const canRestore = () => {
    const a = activePreset();
    return a != null && (!a.is_default || dirty());
  };

  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", "align-items": "center", "margin-bottom": "12px", gap: "10px", "flex-wrap": "wrap" }}>
        {tag("prompt")}
        {eyebrow("switch · edit · restore")}
        <div style={{ flex: 1 }} />
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
            ● edited
          </span>
        </Show>
        <Show when={canRestore()}>
          <button class="btn btn--ghost btn--micro" disabled={busy()} onClick={restore}
            title="Reset the active preset to its shipped default text">
            restore default
          </button>
        </Show>
        <button class="btn btn--primary btn--micro" disabled={busy() || !dirty()} onClick={save}
          title="Save edits to the active preset (agent reloads it live)">
          {busy() ? "…" : "save"}
        </button>
      </div>

      {/* Preset switcher — one chip per preset, the active one highlighted
          (mirrors the font-chip styling in printer_card.tsx). */}
      <div style={{ display: "flex", gap: "8px", "flex-wrap": "wrap", "margin-bottom": "12px" }}>
        <For each={state()?.presets ?? []}>
          {(p) => {
            const selected = () => state()?.active === p.id;
            return (
              <button
                onClick={() => select(p.id)}
                disabled={busy()}
                title={selected() ? `${p.label} (active)` : `switch to ${p.label}`}
                style={{
                  position: "relative",
                  padding: "8px 14px",
                  background: selected() ? "rgba(212, 162, 89, 0.1)" : "var(--c-shell)",
                  border: `1px solid ${selected() ? "var(--c-amber)" : "var(--c-edge)"}`,
                  "border-radius": "10px",
                  cursor: "pointer",
                  color: selected() ? "var(--c-bone)" : "var(--c-mist)",
                  "font-family": "var(--f-sans)",
                  "font-size": "14px",
                  display: "flex",
                  "align-items": "center",
                  gap: "8px",
                  transition: "border-color var(--t-fast), background var(--t-fast)",
                }}
              >
                {p.label}
                <Show when={selected()}>
                  <span class="f-mono" style={{ "font-size": "9px", color: "var(--c-amber)" }}>active</span>
                </Show>
              </button>
            );
          }}
        </For>
      </div>

      <textarea
        class={`field ${dirty() ? "field--dirty" : ""}`}
        rows="12"
        style={{ width: "100%", "font-family": "var(--f-sans)", resize: "vertical", "min-height": "240px" }}
        placeholder="The active preset's system prompt…"
        value={draft()}
        disabled={busy() || !activePreset()}
        onInput={(e) => setDraft(e.currentTarget.value)}
      />

      <div style={{ "font-size": "12px", "line-height": 1.4, color: "var(--c-stone)", "margin-top": "10px", "font-style": "italic" }}>
        The active preset is the agent's live system prompt — switching, saving,
        or restoring it reloads the agent immediately. Edits are saved per
        preset; <strong>restore default</strong> resets the active preset to its
        shipped text. Max 4000 characters.
      </div>
    </div>
  );
};
