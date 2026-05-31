import { Component, createSignal, For, onMount, Show } from "solid-js";
import { api } from "../shared/api";
import { toast } from "../shared/toast";

/** Barebones Wi-Fi switcher — lists the Pi's SAVED networks and lets you flip
 *  to one without SSH (handy when the Pi is on a network this machine isn't).
 *  Switching to a saved profile needs no password; adding a new network is
 *  still done once on the Pi itself. Renders nothing off-Linux / without
 *  NetworkManager (the backend reports `available: false`). */
const cardStyle = {
  padding: "14px",
  "border-radius": "14px",
  background: "rgba(243, 236, 220, 0.02)",
  border: "1px solid var(--c-edge)",
  "margin-bottom": "16px",
} as const;

export const WifiCard: Component = () => {
  const [available, setAvailable] = createSignal(false);
  const [current, setCurrent] = createSignal<string | null>(null);
  const [networks, setNetworks] = createSignal<string[]>([]);
  const [sel, setSel] = createSignal("");
  const [busy, setBusy] = createSignal(false);

  const load = async () => {
    try {
      const r = await api.wifiInfo();
      setAvailable(r.available);
      setCurrent(r.current);
      setNetworks(r.networks);
      setSel(r.current ?? r.networks[0] ?? "");
    } catch {
      setAvailable(false);
    }
  };
  onMount(load);

  const connect = async () => {
    const name = sel();
    if (!name || name === current()) return;
    setBusy(true);
    try {
      await api.wifiConnect(name);
      toast.ok(`switching Wi-Fi → ${name}`, "if this page drops, reconnect on that network");
    } catch (e) {
      toast.warn(`couldn't switch to ${name}`, e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Show when={available()}>
      <div style={cardStyle}>
        <div style={{ display: "flex", "align-items": "center", gap: "8px", "margin-bottom": "10px" }}>
          <span
            class="f-mono"
            style={{
              "font-size": "10px",
              color: "var(--c-stone)",
              "letter-spacing": ".08em",
              "text-transform": "uppercase",
            }}
          >
            network · wi-fi
          </span>
          <span class="f-mono" style={{ "font-size": "11px", color: "var(--c-stone)" }}>
            on: <code style={{ color: "var(--c-cream)" }}>{current() ?? "—"}</code>
          </span>
        </div>
        <div style={{ display: "flex", gap: "8px", "align-items": "center", "flex-wrap": "wrap" }}>
          <select
            class="field"
            style={{ "flex-grow": 1, "min-width": "160px" }}
            value={sel()}
            onChange={(e) => setSel(e.currentTarget.value)}
          >
            <For each={networks()}>
              {(n) => (
                <option value={n}>
                  {n}
                  {n === current() ? " (current)" : ""}
                </option>
              )}
            </For>
          </select>
          <button
            class="btn btn--primary btn--tiny"
            disabled={busy() || !sel() || sel() === current()}
            onClick={connect}
          >
            {busy() ? "switching…" : "Connect"}
          </button>
          <button class="btn btn--ghost btn--micro" disabled={busy()} onClick={load} title="rescan">
            ↻
          </button>
        </div>
        <Show when={networks().length === 0}>
          <div class="f-mono" style={{ "font-size": "11px", color: "var(--c-stone)", "margin-top": "8px" }}>
            no saved networks — add one on the Pi first
          </div>
        </Show>
      </div>
    </Show>
  );
};
