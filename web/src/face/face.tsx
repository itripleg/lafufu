import { Component, createSignal, onCleanup, onMount, Show } from "solid-js";
import { useNavigate } from "@solidjs/router";
import { NatsWs } from "../shared/nats_ws";
import { emotionToColor } from "../shared/design";
import { stateBadge } from "../shared/agent_state";
import { Caption } from "./caption";

const BG_VIDEO_SRC = "/lafufu-bg.mp4";

/** Browsers + TS lib variants. */
type FsDoc = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void>;
};
type FsElem = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void>;
};

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

  // Kiosk control overlay: visible briefly after mouse movement or touch,
  // otherwise hidden so it doesn't pollute the HDMI background.
  const [controlsVisible, setControlsVisible] = createSignal(true);
  const [isFs, setIsFs] = createSignal(false);
  let hideTimer: number | undefined;
  const navigate = useNavigate();

  // First-order smoothing of rms toward a target — same envelope behavior as
  // the old state_blob, but applied to the whole-screen overlay opacity.
  const [pulse, setPulse] = createSignal(0);
  let frame: number | undefined;

  const nats = new NatsWs();
  let videoEl!: HTMLVideoElement;

  const showControlsBriefly = () => {
    setControlsVisible(true);
    if (hideTimer) window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => setControlsVisible(false), 2400);
  };

  const toggleFullscreen = async () => {
    const doc = document as FsDoc;
    const el = document.documentElement as FsElem;
    const inFs = !!(document.fullscreenElement || doc.webkitFullscreenElement);
    try {
      if (inFs) {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (doc.webkitExitFullscreen) await doc.webkitExitFullscreen();
      } else {
        if (el.requestFullscreen) await el.requestFullscreen();
        else if (el.webkitRequestFullscreen) await el.webkitRequestFullscreen();
      }
    } catch {
      /* user-gesture required or browser refused — silently ignore */
    }
  };

  onMount(() => {
    document.body.classList.add("kiosk-cursor-hide");

    // Surface controls on any pointer / key activity; hide them again
    // after a short idle window. Keeps the HDMI background clean.
    const wake = () => showControlsBriefly();
    window.addEventListener("mousemove", wake);
    window.addEventListener("touchstart", wake);
    window.addEventListener("keydown", wake);
    showControlsBriefly();

    const onFsChange = () => {
      const doc = document as FsDoc;
      setIsFs(!!(document.fullscreenElement || doc.webkitFullscreenElement));
    };
    document.addEventListener("fullscreenchange", onFsChange);
    document.addEventListener("webkitfullscreenchange", onFsChange);

    onCleanup(() => {
      window.removeEventListener("mousemove", wake);
      window.removeEventListener("touchstart", wake);
      window.removeEventListener("keydown", wake);
      document.removeEventListener("fullscreenchange", onFsChange);
      document.removeEventListener("webkitfullscreenchange", onFsChange);
      if (hideTimer) window.clearTimeout(hideTimer);
    });

    nats.start();
    // Collect every unsub so onCleanup can drain the listener map. Without
    // this, navigating away from /face and back doubles handlers and runs
    // setState on a disposed component.
    const subs: Array<() => void> = [];
    subs.push(nats.subscribe("agent.state.*", (f) => {
      const tail = f.topic.split(".").pop();
      if (tail) setState(tail);
      if (tail === "idle" || tail === "shutdown") setRms(0);
    }));
    subs.push(nats.subscribe("agent.reply", (f) => {
      setEmotion(f.payload.emotion ?? "neutral");
      setCaption(f.payload.text);
    }));
    subs.push(nats.subscribe("agent.transcript", (f) => {
      setCaption(f.payload.text);
    }));
    subs.push(nats.subscribe("agent.tts.rms", (f) => {
      setRms(f.payload.mouth_target ?? 0);
    }));
    onCleanup(() => subs.forEach((u) => u()));

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
          {stateBadge(state()).label}
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

      {/* Kiosk controls: top-right pill that fades in on activity, fades
          back out after ~2.4s of idle. Lets the operator exit fullscreen
          / jump to /admin without rebooting the Pi. */}
      <div
        style={{
          position: "absolute",
          top: "max(20px, env(safe-area-inset-top))",
          right: "max(20px, env(safe-area-inset-right))",
          display: "flex",
          gap: "6px",
          padding: "6px 8px",
          "border-radius": "999px",
          background: "rgba(20, 16, 12, 0.6)",
          border: "1px solid rgba(243, 236, 220, 0.08)",
          "backdrop-filter": "blur(10px)",
          "-webkit-backdrop-filter": "blur(10px)",
          opacity: controlsVisible() ? 1 : 0,
          transition: "opacity .5s ease",
          "pointer-events": controlsVisible() ? "auto" : "none",
          "z-index": 10,
        }}
      >
        <button
          onClick={toggleFullscreen}
          title={isFs() ? "Exit fullscreen" : "Enter fullscreen"}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--c-mist)",
            cursor: "pointer",
            padding: "4px 8px",
            "font-size": "16px",
            "line-height": 1,
            "border-radius": "999px",
          }}
        >
          {isFs() ? "⤢" : "⛶"}
        </button>
        <button
          onClick={() => navigate("/admin")}
          title="Open admin"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--c-mist)",
            cursor: "pointer",
            padding: "4px 8px",
            "font-size": "13px",
            "font-family": "var(--f-mono)",
            "letter-spacing": ".08em",
            "border-radius": "999px",
          }}
        >
          admin
        </button>
        <button
          onClick={() => navigate("/?stay")}
          title="Back to chooser"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--c-stone)",
            cursor: "pointer",
            padding: "4px 8px",
            "font-size": "16px",
            "line-height": 1,
            "border-radius": "999px",
          }}
        >
          ✕
        </button>
      </div>
    </div>
  );
};

export default Face;
