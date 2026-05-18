import { Component, createSignal, onMount } from "solid-js";
import { useNavigate } from "@solidjs/router";
import { isMobileLikeUA } from "./shared/design";
import { Blob } from "./shared/blob";

/**
 * Quiet splash screen. Auto-suggests the right route based on UA but never
 * navigates without the user clicking — kiosk vs. mobile is a deliberate
 * choice you make once on each device.
 */
export const Landing: Component = () => {
  const navigate = useNavigate();
  const [suggested, setSuggested] = createSignal<"face" | "pet">("face");
  onMount(() => {
    setSuggested(isMobileLikeUA() ? "pet" : "face");
  });

  const card = (
    target: string,
    title: string,
    sub: string,
    accent: string,
    primary = false,
  ) => (
    <button
      onClick={() => navigate(target)}
      style={{
        display: "flex",
        "flex-direction": "column",
        "align-items": "flex-start",
        gap: "10px",
        padding: "26px",
        "min-width": "240px",
        "border-radius": "24px",
        background: primary
          ? "linear-gradient(155deg, rgba(149,176,122,0.18), rgba(149,176,122,0.04))"
          : "rgba(243, 236, 220, 0.04)",
        border: `1px solid ${primary ? "rgba(149,176,122,0.4)" : "var(--c-edge)"}`,
        color: "var(--c-bone)",
        cursor: "pointer",
        "text-align": "left",
        transition: "transform 280ms cubic-bezier(.34,1.56,.64,1), background 220ms ease",
      }}
      onMouseOver={(e) => (e.currentTarget.style.transform = "translateY(-3px)")}
      onMouseOut={(e) => (e.currentTarget.style.transform = "translateY(0)")}
    >
      <div class="eyebrow" style={{ color: accent }}>{title}</div>
      <div
        class="f-display"
        style={{
          "font-size": "44px",
          "line-height": .96,
        }}
      >
        {target.slice(1)}
      </div>
      <div
        style={{
          color: "var(--c-mist)",
          "font-size": "13px",
          "line-height": 1.45,
          "max-width": "260px",
        }}
      >
        {sub}
      </div>
    </button>
  );

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        "align-items": "center",
        "justify-content": "center",
        background:
          "radial-gradient(ellipse at 30% 20%, #2a2018 0%, #1a1410 60%, #0c0907 100%)",
        /* `clip` is stricter than `hidden` for animated transforms on iOS. */
        overflow: "clip",
      }}
    >
      <Blob size="60vmin" color="var(--c-amber)" opacity={.18} blur={80}
            style={{ top: "-15vmin", left: "-15vmin" }} drift />
      <Blob size="48vmin" color="var(--c-moss)" opacity={.14} blur={70} variant={2}
            style={{ bottom: "-10vmin", right: "-12vmin" }} drift delay={5} />

      <div style={{ position: "relative", "max-width": "1080px", padding: "0 28px" }}>
        <div class="eyebrow" style={{ "margin-bottom": "16px" }}>
          tinker studio · v0.2.0
        </div>
        <h1
          class="f-display"
          style={{
            "font-size": "clamp(72px, 13vw, 200px)",
            margin: "0 0 8px",
            color: "var(--c-bone)",
            "letter-spacing": "-0.03em",
          }}
        >
          lafufu
        </h1>
        <p
          style={{
            "max-width": "520px",
            "font-size": "16px",
            "line-height": 1.5,
            color: "var(--c-mist)",
            "margin-bottom": "36px",
          }}
        >
          A little mischievous creature on a raspberry pi, with five servos, a
          microphone, a printer, and an opinion. Pick a window in.
        </p>

        <div style={{ display: "flex", gap: "16px", "flex-wrap": "wrap" }}>
          {card(
            "/face",
            "/face · hdmi kiosk",
            "Full-screen ambient face for the display on the pi. Background video, breathing emotion overlay, live captions.",
            "var(--c-amber)",
            suggested() === "face",
          )}
          {card(
            "/pet",
            "/pet · pocket tamagotchi",
            "Interactive labubu in your hand. Drag to rotate, tap to play. Mirrors live servo pose. Easter eggs hidden inside.",
            "var(--c-moss)",
            suggested() === "pet",
          )}
          {card(
            "/admin",
            "/admin · control deck",
            "Tunables, expressions, chat, system pulse. For the operator.",
            "var(--c-mauve)",
          )}
        </div>
      </div>
    </div>
  );
};
