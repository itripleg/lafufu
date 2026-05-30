import { Component, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { Blob } from "../shared/blob";
import { lsGet, lsKeys, lsRemovePrefix, lsSet } from "../shared/local_storage";
import { toast } from "../shared/toast";
import { HeadPanel } from "./head_panel";
import { Companion } from "./companion";
import { ServiceStatus } from "./service_status";
import { SettingsForm } from "./settings_form";
import { StudioSection } from "./studio_section";
import { SystemPulse } from "./system_pulse";
import { useLayoutMode } from "../shared/use_media";

/**
 * Control deck — premium organic surface, packs everything an operator (or
 * developer) needs in a single pane. Layout is asymmetric on desktop and
 * stacks cleanly on mobile.
 */
type AdminTab = "chat" | "head" | "studio" | "settings" | "status";

// Head tab is hidden for now (the Studio supersedes it). The panel + code are
// kept and still mount under tab() === "head"; just re-add the entry here to
// restore the button.
const ADMIN_TABS: { id: AdminTab; label: string; accent: string }[] = [
  { id: "chat", label: "Chat", accent: "var(--c-moss)" },
  { id: "studio", label: "Studio", accent: "var(--c-coral)" },
  { id: "settings", label: "Settings", accent: "var(--c-amber)" },
  { id: "status", label: "Status", accent: "var(--c-mauve)" },
];

const Admin: Component = () => {
  const nats = new NatsWs();
  const layout = useLayoutMode();
  const [draftCount, setDraftCount] = createSignal(0);
  const [connState, setConnState] = createSignal<"live" | "pending">("pending");
  // Guard against a stale persisted tab id (e.g. the old "body") so a renamed
  // tab can't leave the deck blank — fall back to chat if it's not a known tab.
  const storedTab = lsGet<AdminTab>("admin/tab", "chat");
  const initialTab: AdminTab = ADMIN_TABS.some((t) => t.id === storedTab) ? storedTab : "chat";
  const [tab, setTab] = createSignal<AdminTab>(initialTab);
  const setActiveTab = (t: AdminTab) => { setTab(t); lsSet("admin/tab", t); };

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
    // Only wipe settings drafts — leave chat/compose/pulse-filter keys alone
    // since those are user content, not pending settings edits.
    const n = lsRemovePrefix("settings/draft/");
    window.dispatchEvent(new CustomEvent("lafufu:drafts-changed"));
    window.dispatchEvent(new CustomEvent("lafufu:drafts-wiped"));
    toast.ok("settings drafts cleared", `${n} pending edit${n === 1 ? "" : "s"} discarded`);
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

      {/* TABBED CONTROL GROUP — chat · body · settings.
          First thing on the page; one panel visible at a time so the three
          sections are symmetrical in size. All three stay mounted (display
          toggle, not <Show>) so chat history, live pose, and NATS
          subscriptions survive tab switches. */}
      <div
        role="tablist"
        aria-label="control sections"
        style={{
          display: "flex",
          gap: layout() === "mobile" ? "3px" : "5px",
          padding: layout() === "mobile" ? "3px" : "5px",
          background: "var(--c-shell)",
          border: "1px solid var(--c-edge)",
          "border-radius": "16px",
          "margin-bottom": "16px",
        }}
      >
        <For each={ADMIN_TABS}>
          {(t) => {
            const active = () => tab() === t.id;
            const showDraftDot = () => t.id === "settings" && draftCount() > 0;
            const mobile = () => layout() === "mobile";
            return (
              <button
                role="tab"
                aria-selected={active()}
                onClick={() => setActiveTab(t.id)}
                style={{
                  position: "relative",
                  flex: "1 1 0",
                  /* Let the flex children shrink past their content width so
                     four tabs always fit one row on a ~360px phone. */
                  "min-width": 0,
                  display: "flex",
                  "align-items": "center",
                  "justify-content": "center",
                  gap: mobile() ? "5px" : "8px",
                  padding: mobile() ? "10px 6px" : "13px 16px",
                  "border-radius": "12px",
                  border: `1px solid ${active() ? t.accent : "transparent"}`,
                  background: active()
                    ? `linear-gradient(160deg, ${t.accent}26, ${t.accent}0d)`
                    : "transparent",
                  color: active() ? "var(--c-bone)" : "var(--c-mist)",
                  "font-family": "var(--f-display-roman, var(--f-display))",
                  "font-size": mobile() ? "13px" : "16px",
                  "letter-spacing": ".01em",
                  cursor: "pointer",
                  transition:
                    "background var(--t-fast), border-color var(--t-fast), color var(--t-fast)",
                }}
                onMouseOver={(e) => {
                  if (!active()) e.currentTarget.style.color = "var(--c-bone)";
                }}
                onMouseOut={(e) => {
                  if (!active()) e.currentTarget.style.color = "var(--c-mist)";
                }}
              >
                {/* The status dot is decorative; drop it on phones to buy the
                    label the width it needs. */}
                <Show when={!mobile()}>
                  <span
                    style={{
                      width: "7px",
                      height: "7px",
                      "flex-shrink": 0,
                      "border-radius": "50%",
                      background: active() ? t.accent : "var(--c-edge)",
                      "box-shadow": active() ? `0 0 8px ${t.accent}` : "none",
                      transition: "background var(--t-fast), box-shadow var(--t-fast)",
                    }}
                  />
                </Show>
                {t.label}
                {/* Absolutely positioned so the unsaved-draft count never
                    nudges the tab label as it appears/disappears. */}
                <Show when={showDraftDot()}>
                  {/* On phones the tab is too narrow for the count to sit beside
                      a centered label without overlapping it — show a small amber
                      dot instead; the exact number still lives in the header. */}
                  <Show
                    when={!mobile()}
                    fallback={
                      <span
                        style={{
                          position: "absolute",
                          top: "6px",
                          right: "6px",
                          width: "6px",
                          height: "6px",
                          "border-radius": "50%",
                          background: "var(--c-amber)",
                          "box-shadow": "0 0 4px var(--c-amber)",
                          "pointer-events": "none",
                        }}
                      />
                    }
                  >
                    <span
                      class="f-mono"
                      style={{
                        position: "absolute",
                        top: "50%",
                        right: "10px",
                        transform: "translateY(-50%)",
                        "font-size": "10px",
                        color: "var(--c-amber)",
                        "letter-spacing": ".04em",
                        "pointer-events": "none",
                      }}
                    >
                      {draftCount()}
                    </span>
                  </Show>
                </Show>
              </button>
            );
          }}
        </For>
      </div>

      <div>
        <div style={{ display: tab() === "chat" ? "block" : "none" }}>
          <Companion nats={nats} />
        </div>
        <div style={{ display: tab() === "head" ? "block" : "none" }}>
          <HeadPanel nats={nats} />
        </div>
        <div style={{ display: tab() === "studio" ? "block" : "none" }}>
          <StudioSection nats={nats} />
        </div>
        <div style={{ display: tab() === "settings" ? "block" : "none" }}>
          <SettingsForm onDraftCountChange={refreshDraftCount} />
        </div>
        {/* Status / debug — service health + raw NATS firehose. */}
        <div style={{ display: tab() === "status" ? "block" : "none" }}>
          <div style={{ "margin-bottom": "20px" }}>
            <ServiceStatus nats={nats} />
          </div>
          <SystemPulse nats={nats} />
        </div>
      </div>
    </div>
  );
};

export default Admin;
