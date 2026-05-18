import { Component, createSignal, onCleanup, onMount, Show } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { emotionToColor } from "../shared/design";
import { Caption } from "./caption";

const BG_VIDEO_SRC = "/lafufu-bg.mp4";

/**
 * Kiosk view — full-bleed background mp4 with an emotion-tinted overlay that
 * brightens/breathes with the TTS RMS. Caption surfaces what the agent just
 * said/heard. Designed to fill the HDMI panel attached to the Pi.
 */
const Face: Component = () => {
  const [state, setState] = createSignal<string>("idle");
  const [emotion, setEmotion] = createSignal<string>("neutral");
  const [rms, setRms] = createSignal<number>(0);
  const [caption, setCaption] = createSignal<string | undefined>();
  const [bgReady, setBgReady] = createSignal(false);
  const [bgError, setBgError] = createSignal(false);

  // First-order smoothing of rms toward a target — same envelope behavior as
  // the old state_blob, but applied to the whole-screen overlay opacity.
  const [pulse, setPulse] = createSignal(0);
  let frame: number | undefined;

  const nats = new NatsWs();
  let videoEl!: HTMLVideoElement;

  onMount(() => {
    document.body.classList.add("kiosk-cursor-hide");

    nats.start();
    nats.subscribe("agent.state.*", (f) => {
      const tail = f.topic.split(".").pop();
      if (tail) setState(tail);
      if (tail === "idle" || tail === "shutdown") setRms(0);
    });
    nats.subscribe("agent.reply", (f) => {
      setEmotion(f.payload.emotion ?? "neutral");
      setCaption(f.payload.text);
    });
    nats.subscribe("agent.transcript", (f) => {
      setCaption(f.payload.text);
    });
    nats.subscribe("agent.tts.rms", (f) => {
      setRms(f.payload.mouth_target ?? 0);
    });

    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setPulse((p) => p + (rms() - p) * Math.min(1, dt * 8));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    // Safari/iOS autoplay safety — manually kick after metadata loads.
    videoEl?.play?.().catch(() => { /* user gesture may be required */ });
  });
  onCleanup(() => {
    document.body.classList.remove("kiosk-cursor-hide");
    if (frame) cancelAnimationFrame(frame);
    nats.stop();
  });

  const tint = () => emotionToColor(emotion());
  const overlayOpacity = () => 0.22 + pulse() * 0.45;
  const vignettePulse  = () => 0.55 + pulse() * 0.25;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        overflow: "hidden",
        background: "#0a0806",
      }}
    >
      {/* BG: looping mp4. Hidden until first frame paints to avoid black flash. */}
      <video
        ref={videoEl}
        autoplay
        muted
        loop
        playsinline
        preload="auto"
        onCanPlay={() => setBgReady(true)}
        onError={() => setBgError(true)}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          "object-fit": "cover",
          opacity: bgReady() ? 1 : 0,
          transition: "opacity 1.2s ease",
          filter: "saturate(1.05) contrast(1.02)",
        }}
      >
        <source src={BG_VIDEO_SRC} type="video/mp4" />
      </video>

      {/* Procedural fallback if the mp4 is missing — keeps kiosk presentable */}
      <Show when={bgError()}>
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "radial-gradient(ellipse at 50% 40%, #3a2d22 0%, #1a1410 70%, #0a0806 100%)",
          }}
        />
      </Show>

      {/* Emotion-tinted breathing overlay — soft radial that pulses with TTS */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `radial-gradient(ellipse at 50% 45%, ${tint()}88 0%, transparent 55%)`,
          opacity: overlayOpacity(),
          "mix-blend-mode": "soft-light",
          transition: "background 0.6s ease",
          "pointer-events": "none",
        }}
      />

      {/* Vignette + film-grain band at edges to anchor the typography */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at center, transparent 45%, rgba(10,8,6,0.9) 100%)",
          opacity: vignettePulse(),
          "pointer-events": "none",
        }}
      />

      {/* State label — quiet upper register */}
      <div
        style={{
          position: "absolute",
          top: "max(40px, env(safe-area-inset-top))",
          left: 0, right: 0,
          display: "flex",
          "justify-content": "center",
          "pointer-events": "none",
        }}
      >
        <div
          class="eyebrow"
          style={{
            color: tint(),
            "text-shadow": "0 1px 12px rgba(0,0,0,.6)",
          }}
        >
          {state()}
        </div>
      </div>

      {/* The big display word — emotion glyph word, italic Fraunces */}
      <Show when={!caption() && state() === "idle"}>
        <div
          class="f-display"
          style={{
            position: "absolute",
            top: "50%", left: "50%",
            transform: "translate(-50%, -50%)",
            "font-size": "clamp(120px, 18vw, 360px)",
            color: "var(--c-bone)",
            opacity: 0.92,
            "text-shadow": "0 6px 40px rgba(0,0,0,.55)",
            "pointer-events": "none",
            "user-select": "none",
            "letter-spacing": "-0.02em",
            animation: "breathe 5.5s ease-in-out infinite",
          }}
        >
          lafufu
        </div>
      </Show>

      <Caption text={caption} tint={tint} />
    </div>
  );
};

export default Face;
