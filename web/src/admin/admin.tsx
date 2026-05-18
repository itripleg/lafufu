import { Component, createSignal, onCleanup, onMount, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { Blob } from "../shared/blob";
import { lsClearAll, lsKeys } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { BodyPanel } from "./body_panel";
import { ChatLog } from "./chat_log";
import { ServiceStatus } from "./service_status";
import { SettingsForm } from "./settings_form";
import { SystemPulse } from "./system_pulse";

/**
 * Control deck — premium organic surface, packs everything an operator (or
 * developer) needs in a single pane. Layout is asymmetric on desktop and
 * stacks cleanly on mobile.
 */
const Admin: Component = () => {
  const nats = new NatsWs();
  const [draftCount, setDraftCount] = createSignal(0);
  const [connState, setConnState] = createSignal<"live" | "pending">("pending");

  const refreshDraftCount = () => {
    setDraftCount(lsKeys().filter((k) => k.startsWith("settings/draft/")).length);
  };

  onMount(() => {
    nats.start();
    refreshDraftCount();
    window.addEventListener("storage", refreshDraftCount);
    window.addEventListener("lafufu:drafts-changed", refreshDraftCount);
    // Heuristic: if any heartbeat arrives within 3s, we say "live".
    const off = nats.subscribe("system.heartbeat.*", () => setConnState("live"));
    onCleanup(off);
  });
  onCleanup(() => {
    nats.stop();
    window.removeEventListener("storage", refreshDraftCount);
    window.removeEventListener("lafufu:drafts-changed", refreshDraftCount);
  });

  const wipeAllDrafts = () => {
    if (draftCount() === 0) {
      toast.info("no drafts to clear");
      return;
    }
    const n = draftCount();
    lsClearAll();
    window.dispatchEvent(new CustomEvent("lafufu:drafts-changed"));
    window.dispatchEvent(new CustomEvent("lafufu:drafts-wiped"));
    toast.ok("local drafts cleared", `${n} pending edit${n === 1 ? "" : "s"} discarded`);
  };

  return (
    <div
      style={{
        position: "relative",
        "min-height": "100vh",
        background:
          "radial-gradient(ellipse at 20% -10%, #2a2018 0%, #1a1410 50%, #0c0907 100%)",
        "padding": "32px clamp(16px, 4vw, 56px) 80px",
        "max-width": "1600px",
        margin: "0 auto",
        /* Belt-and-suspenders: clip the decorative blobs locally so any
           animated transform / blur halo can't escape this container. */
        overflow: "hidden",
      }}
    >
      <Blob size="50vmin" color="var(--c-amber)" opacity={.08} blur={90}
            style={{ top: "-20vmin", right: "-20vmin" }} drift />
      <Blob size="40vmin" color="var(--c-moss)" opacity={.07} blur={90} variant={2}
            style={{ bottom: "-15vmin", left: "-15vmin" }} drift delay={4} />

      {/* HEADER ---------------------------------------------------------- */}
      <header
        style={{
          position: "relative",
          display: "flex",
          "flex-wrap": "wrap",
          "align-items": "flex-end",
          "justify-content": "space-between",
          gap: "24px",
          "margin-bottom": "36px",
          "padding-bottom": "20px",
          "border-bottom": "1px solid var(--c-edge)",
          /* Title + actions together easily exceed a phone's width — without
             wrap + min-width allowance the header forces page-wide overflow. */
          "min-width": 0,
          "max-width": "100%",
        }}
      >
        <div>
          <div class="eyebrow" style={{ "margin-bottom": "10px" }}>
            tinker studio · control deck
          </div>
          <h1
            class="f-display"
            style={{
              "font-size": "clamp(48px, 7vw, 96px)",
              margin: 0,
              color: "var(--c-bone)",
              "letter-spacing": "-0.025em",
            }}
          >
            lafufu
            <span
              style={{
                color: "var(--c-amber)",
                "margin-left": "16px",
                "font-size": ".4em",
                "font-style": "normal",
                "vertical-align": "middle",
                "font-family": "var(--f-mono)",
                "letter-spacing": ".05em",
              }}
            >
              v0.2.0
            </span>
          </h1>
        </div>
        <div
          style={{
            display: "flex",
            gap: "10px",
            "align-items": "center",
            "flex-wrap": "wrap",
            "justify-content": "flex-end",
          }}
        >
          <div
            style={{
              display: "flex",
              "align-items": "center",
              gap: "8px",
              padding: "6px 12px",
              "border-radius": "999px",
              border: "1px solid var(--c-edge)",
              background: "rgba(243,236,220,.03)",
              "font-family": "var(--f-mono)",
              "font-size": "11px",
              color: "var(--c-mist)",
            }}
            title={connState() === "live" ? "NATS bridge online" : "waiting for first heartbeat"}
          >
            <span
              style={{
                width: "8px", height: "8px",
                "border-radius": "50%",
                background: connState() === "live" ? "var(--c-moss)" : "var(--c-amber)",
                "box-shadow": connState() === "live"
                  ? "0 0 8px var(--c-moss)"
                  : "0 0 6px var(--c-amber)",
                animation: connState() === "live" ? "breathe 2.4s ease-in-out infinite" : undefined,
              }}
            />
            {connState() === "live" ? "live" : "waiting…"}
          </div>

          <Show when={draftCount() > 0}>
            <button
              class="btn btn--coral btn--tiny"
              onClick={wipeAllDrafts}
              title="Discard all unsaved local drafts"
            >
              wipe {draftCount()} draft{draftCount() === 1 ? "" : "s"}
            </button>
          </Show>

          <a href="/face" class="btn btn--ghost btn--tiny">→ /face</a>
          <a href="/pet"  class="btn btn--ghost btn--tiny">→ /pet</a>
        </div>
      </header>

      {/* Services strip — thin horizontal row at the very top. */}
      <div style={{ "margin-bottom": "20px" }}>
        <ServiceStatus nats={nats} />
      </div>

      {/* Body panel — full width below, consolidates Pose / Expressions /
          Sliders. Sliders double as live pose readout. */}
      <div style={{ "margin-bottom": "20px" }}>
        <BodyPanel nats={nats} />
      </div>

      {/* CHAT + SETTINGS — side by side on wide screens --------------- */}
      <div class="cards-grid--wide" style={{ "margin-bottom": "20px" }}>
        <ChatLog nats={nats} />
        <SettingsForm onDraftCountChange={refreshDraftCount} />
      </div>

      {/* SYSTEM PULSE — full width ---------------------------------- */}
      <SystemPulse nats={nats} />
    </div>
  );
};

export default Admin;
