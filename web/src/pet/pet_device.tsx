import { Component, createEffect, createMemo, createSignal, onCleanup, onMount, Show } from "solid-js";
import type { NatsWs } from "../shared/nats_ws";
import { api, type ExpressionDTO, type FrameDTO } from "../shared/api";
import { emotionToColor } from "../shared/design";
import { applyDragDelta, axisMid, type DraggableAxis, type ServoRanges } from "./head_drag";
import { useServoConfig } from "../shared/use_servo_config";

/**
 * PetDevice — the Tamagotchi-lite "handheld": Lafufu in a little LCD screen
 * wearing the inked sprite for its current emotion (idle when idle, angry when
 * angry, …). Background-agnostic and fills its container, so it works both
 * full-screen on /pet and embedded beside the chat.
 *
 * Live emotion comes over NATS (`agent.reply` → emotion tag, `agent.state.*` →
 * idle/speaking/…). Dragging tilts the card AND drives the head servos; live
 * `animator.pose` echoes are adopted only on axes the user isn't dragging.
 *
 * The caller owns the NatsWs lifecycle (start/stop) — PetDevice only subscribes.
 */

const MAX_YAW_DEG = 28;
const MAX_PITCH_DEG = -22;
const GRACE_MS = 800;
// Hold the last mood briefly after going idle, then relax to a neutral face.
const REST_MS = 6000;

const EMOTION_SPRITE: Record<string, string> = {
  happy:     "idle_01.png",
  sad:       "idle_07.png",
  angry:     "idle_06.png",
  surprised: "idle_01.png",
  neutral:   "idle_01.png",
  agree:     "laugh_01.png",
  disagree:  "idle_06.png",
};
const spriteUrl = (emotion: string) =>
  api.imageFileUrl("sprites", "default", EMOTION_SPRITE[emotion] ?? EMOTION_SPRITE.neutral);

/** Resolve a "bucket/kind/name" frame image ref to a servable URL. */
const refToUrl = (ref: string | null): string | null => {
  if (!ref) return null;
  const parts = ref.split("/");
  if (parts.length !== 3) return null;
  const [bucket, kind, name] = parts;
  if (bucket !== "letterheads" && bucket !== "sprites") return null;
  return api.imageFileUrl(bucket as "letterheads" | "sprites", kind, name);
};

export const PetDevice: Component<{ nats: NatsWs }> = (props) => {
  const config = useServoConfig(props.nats);

  // ── emotion / state ──
  const [emotion, setEmotion] = createSignal<string>("neutral");
  const [agentState, setAgentState] = createSignal<string>("idle");
  const [popping, setPopping] = createSignal(false);

  const resting = createMemo(() => agentState() === "idle" && emotion() === "neutral");
  const tint = createMemo(() => emotionToColor(emotion()));
  const statusText = createMemo(() => (resting() ? "idle" : emotion()));

  let restTimer: ReturnType<typeof setTimeout> | undefined;
  const setMood = (emo: string) => {
    if (restTimer) { clearTimeout(restTimer); restTimer = undefined; }
    setEmotion(emo);
  };
  let firstMood = true;
  createEffect(() => {
    emotion();
    if (firstMood) { firstMood = false; return; }
    setPopping(true);
    setTimeout(() => setPopping(false), 240);
  });

  // ── screen visual: single display-media OR per-frame flipbook ──
  // Each emotion's expression may carry a single `display_media` (one image or
  // mp4) shown on the screen instead of cycling the per-frame images. When set,
  // the flipbook is skipped and the one media drives the visual; the servos
  // still animate frame-by-frame (driven by the animator over `animator.pose`).
  // When unset — or the media file is missing (onError) — we fall back to the
  // per-frame flipbook, then to the static per-emotion sprite.
  const [playImage, setPlayImage] = createSignal<string | null>(null);
  // Resolved URL of the current emotion's single media, or null for flipbook.
  const [mediaUrl, setMediaUrl] = createSignal<string | null>(null);
  // Set when the single media fails to load → drop back to the flipbook.
  const [mediaError, setMediaError] = createSignal(false);
  const mediaIsVideo = createMemo(() => {
    const u = mediaUrl();
    return !!u && /\.mp4(\?|$)/i.test(u);
  });
  // Resting moods loop their clip (it's the ambient screen); a fired emotion's
  // mp4 plays once then hands back to neutral (see onEnded below).
  const isRestingEmotion = (emo: string) => emo === "neutral" || emo === "idle";

  let exprList: ExpressionDTO[] = [];
  let frameMap = new Map<string, FrameDTO>();
  let playbackTimer: ReturnType<typeof setTimeout> | undefined;
  const stopFramePlayback = () => {
    if (playbackTimer !== undefined) { clearTimeout(playbackTimer); playbackTimer = undefined; }
  };
  const playEmotionFrames = (emo: string) => {
    stopFramePlayback();
    // The animator resolves an emotion to the expression of the same name.
    const expr = exprList.find((e) => e.name === emo);

    // Single-media mode: one image/mp4 takes over the screen. Skip the flipbook
    // entirely (unless the media already failed to load this emotion).
    const media = expr?.display_media ? refToUrl(expr.display_media) : null;
    if (media && !mediaError()) {
      setMediaUrl(media);
      setPlayImage(null);
      return;
    }
    setMediaUrl(null);

    if (!expr || expr.playback === "random_walk" || expr.steps.length === 0) {
      setPlayImage(null); // → falls back to the static per-emotion sprite
      return;
    }
    const run = (idx: number) => {
      const step = expr.steps[idx];
      const fr = step ? frameMap.get(step.frame) : undefined;
      const delay = step?.delay_ms ?? expr.default_delay_ms ?? 0;
      const duration = step?.duration_ms ?? expr.default_duration_ms ?? 200;
      playbackTimer = setTimeout(() => {
        if (fr) setPlayImage(refToUrl(fr.image));
        playbackTimer = setTimeout(() => {
          const next = idx + 1;
          if (next >= expr.steps.length) {
            if (expr.playback === "loop") run(0);
            else setPlayImage(null); // "once" finished → settle to the sprite
          } else run(next);
        }, duration);
      }, delay);
    };
    run(0);
  };
  // Reset the media-load failure flag whenever the emotion changes so a new
  // emotion gets a fresh attempt at its own clip.
  createEffect((prev) => {
    const emo = emotion();
    if (prev !== undefined && prev !== emo) setMediaError(false);
    return emo;
  });
  createEffect(() => { mediaError(); playEmotionFrames(emotion()); });
  onCleanup(stopFramePlayback);

  // ── head tilt / servo drive ──
  const [headLr, setHeadLr] = createSignal<number | undefined>(undefined);
  const [headUd, setHeadUd] = createSignal<number | undefined>(undefined);
  const [dragging, setDragging] = createSignal(false);

  let lastPose: Record<string, number> = {};
  const axisHoldTs: Record<DraggableAxis, number> = { head_lr: 0, head_ud: 0, eye: 0, jaw: 0 };
  const axisOwned = (a: DraggableAxis) => performance.now() - axisHoldTs[a] < GRACE_MS;

  createEffect(() => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!ranges) return;
    if (headLr() === undefined) setHeadLr(lastPose.head_lr ?? axisMid("head_lr", ranges));
    if (headUd() === undefined) setHeadUd(lastPose.head_ud ?? axisMid("head_ud", ranges));
  });

  const signedFor = (dxl: number, axis: DraggableAxis): number => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!ranges) return 0;
    const [lo, hi] = ranges[axis];
    const n = Math.max(0, Math.min(1, (dxl - lo) / (hi - lo)));
    return n * 2 - 1;
  };

  let previewTimer: ReturnType<typeof setTimeout> | undefined;
  const pending: Partial<Record<DraggableAxis, number>> = {};
  const flushPreview = () => {
    previewTimer = undefined;
    for (const k of Object.keys(pending) as DraggableAxis[]) {
      const v = pending[k];
      if (v !== undefined) { api.animatorPreview(k, Math.round(v)).catch(() => {}); delete pending[k]; }
    }
  };
  const queuePreview = (axis: DraggableAxis, value: number) => {
    pending[axis] = value;
    if (previewTimer === undefined) previewTimer = setTimeout(flushPreview, 40);
  };

  const transform = createMemo(() => {
    const lr = headLr();
    const ud = headUd();
    const scale = popping() ? 1.07 : 1;
    if (lr === undefined || ud === undefined) return `scale(${scale})`;
    const yawDeg = signedFor(lr, "head_lr") * MAX_YAW_DEG;
    const pitchDeg = signedFor(ud, "head_ud") * MAX_PITCH_DEG;
    return `perspective(900px) rotateX(${pitchDeg}deg) rotateY(${yawDeg}deg) scale(${scale})`;
  });

  let lastX = 0, lastY = 0;
  const onPointerDown = (e: PointerEvent) => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!ranges) return;
    setDragging(true);
    lastX = e.clientX; lastY = e.clientY;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setHeadLr(lastPose.head_lr ?? axisMid("head_lr", ranges));
    setHeadUd(lastPose.head_ud ?? axisMid("head_ud", ranges));
  };
  const onPointerMove = (e: PointerEvent) => {
    const ranges = config()?.ranges as ServoRanges | undefined;
    if (!dragging() || !ranges) return;
    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    const newLr = applyDragDelta("head_lr", headLr() ?? axisMid("head_lr", ranges), dx, ranges);
    const newUd = applyDragDelta("head_ud", headUd() ?? axisMid("head_ud", ranges), dy, ranges);
    setHeadLr(newLr); setHeadUd(newUd);
    const now = performance.now();
    axisHoldTs.head_lr = now; axisHoldTs.head_ud = now;
    queuePreview("head_lr", newLr); queuePreview("head_ud", newUd);
  };
  const onPointerUp = () => {
    if (!dragging()) return;
    setDragging(false);
    const now = performance.now();
    axisHoldTs.head_lr = now; axisHoldTs.head_ud = now;
    if (previewTimer !== undefined) { clearTimeout(previewTimer); previewTimer = undefined; }
    flushPreview();
  };

  onMount(() => {
    // Dev/preview override: ?emotion=angry seeds a face (a real reply overrides).
    const preview = new URLSearchParams(window.location.search).get("emotion");
    if (preview && preview in EMOTION_SPRITE) setEmotion(preview);

    // Load expressions + frames so emotions can animate through their frame
    // images (same data the studio plays). A failed load just leaves the pet
    // on the static per-emotion sprite.
    void Promise.all([api.listExpressions(), api.listFrames()])
      .then(([ex, fr]) => {
        exprList = ex.items;
        frameMap = new Map(fr.items.map((f) => [f.name, f]));
        playEmotionFrames(emotion()); // catch up now that data is loaded
      })
      .catch(() => { /* no frame playback — stays on the emotion sprite */ });

    const subs: Array<() => void> = [];
    subs.push(props.nats.subscribe("animator.pose", (f) => {
      lastPose = f.payload;
      if (!axisOwned("head_lr") && typeof f.payload.head_lr === "number") setHeadLr(f.payload.head_lr);
      if (!axisOwned("head_ud") && typeof f.payload.head_ud === "number") setHeadUd(f.payload.head_ud);
    }));
    // Re-fetch expressions when any expression changes in the Studio so that
    // display_media edits become visible on the pet screen without a reload.
    subs.push(props.nats.subscribe("expressions.changed", () => {
      void api.listExpressions()
        .then((ex) => {
          exprList = ex.items;
          playEmotionFrames(emotion());
        })
        .catch(() => { /* keep stale list on transient error */ });
    }));
    subs.push(props.nats.subscribe("agent.reply", (f) => setMood(f.payload?.emotion ?? "neutral")));
    subs.push(props.nats.subscribe("agent.state.*", (f) => {
      const tail = f.topic.split(".").pop();
      if (!tail) return;
      setAgentState(tail);
      if (tail === "idle" || tail === "shutdown") {
        if (restTimer) clearTimeout(restTimer);
        restTimer = setTimeout(() => setEmotion("neutral"), REST_MS);
      }
    }));
    onCleanup(() => subs.forEach((u) => u()));
  });

  onCleanup(() => {
    if (previewTimer !== undefined) clearTimeout(previewTimer);
    if (restTimer) clearTimeout(restTimer);
  });

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        display: "flex",
        "align-items": "center",
        "justify-content": "center",
        overflow: "hidden",
        "touch-action": "none",
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <div
        style={{
          position: "relative",
          width: "min(100%, 440px)",
          padding: "20px 20px 14px",
          "border-radius": "40px",
          background: "linear-gradient(168deg, #2a2119 0%, #1c1611 100%)",
          border: "1px solid rgba(243,236,220,.08)",
          "box-shadow": `0 30px 70px -28px rgba(0,0,0,.8), 0 0 60px -10px ${tint()}44, inset 0 1px 0 rgba(255,240,210,.05)`,
          transition: "box-shadow 0.6s ease",
        }}
      >
        <div
          style={{
            position: "relative",
            "aspect-ratio": "1",
            "border-radius": "26px",
            overflow: "hidden",
            background: "#0c0907",
            border: `2px solid ${tint()}66`,
            "box-shadow": "inset 0 2px 18px rgba(0,0,0,.6)",
            display: "flex",
            "align-items": "center",
            "justify-content": "center",
          }}
        >
          <div
            style={{
              width: "100%",
              height: "100%",
              animation: resting() && !dragging() ? "breathe 5s ease-in-out infinite" : "none",
            }}
          >
            <Show
              when={mediaIsVideo()}
              fallback={
                <img
                  src={mediaUrl() ?? playImage() ?? (config() ? spriteUrl(emotion()) : "/lafufu.png")}
                  alt={`lafufu ${statusText()}`}
                  draggable={false}
                  onError={() => { if (mediaUrl()) setMediaError(true); }}
                  style={{
                    width: "100%",
                    height: "100%",
                    "object-fit": "cover",
                    transform: transform(),
                    "transform-style": "preserve-3d",
                    transition: dragging() ? "none" : "transform 0.3s ease-out",
                    "user-select": "none",
                    "pointer-events": "none",
                    opacity: config() ? 1 : 0.55,
                  }}
                />
              }
            >
              <video
                src={mediaUrl()!}
                autoplay
                muted
                playsinline
                loop={isRestingEmotion(emotion())}
                onError={() => setMediaError(true)}
                onEnded={() => { if (!isRestingEmotion(emotion())) setMood("neutral"); }}
                style={{
                  width: "100%",
                  height: "100%",
                  "object-fit": "cover",
                  transform: transform(),
                  "transform-style": "preserve-3d",
                  transition: dragging() ? "none" : "transform 0.3s ease-out",
                  "user-select": "none",
                  "pointer-events": "none",
                  opacity: config() ? 1 : 0.55,
                }}
              />
            </Show>
          </div>
          <div
            style={{
              position: "absolute",
              inset: 0,
              "pointer-events": "none",
              background: `radial-gradient(ellipse at 50% 38%, ${tint()}18 0%, transparent 60%)`,
              "mix-blend-mode": "soft-light",
            }}
          />
        </div>

        <div
          style={{
            display: "flex",
            "align-items": "center",
            "justify-content": "center",
            gap: "9px",
            "margin-top": "12px",
            "font-family": "ui-monospace, monospace",
            "font-size": "0.78rem",
            "letter-spacing": "0.14em",
            "text-transform": "uppercase",
            color: "rgba(243,236,220,.82)",
          }}
        >
          <span
            style={{
              width: "9px",
              height: "9px",
              "border-radius": "50%",
              background: tint(),
              "box-shadow": `0 0 10px ${tint()}`,
              animation: resting() ? "breathe 3s ease-in-out infinite" : "none",
            }}
          />
          {statusText()}
        </div>
      </div>

      <Show when={!config()}>
        <div
          style={{
            position: "absolute",
            bottom: "5%",
            "font-family": "ui-monospace, monospace",
            "font-size": "0.8rem",
            color: "rgba(220,200,170,0.55)",
            "letter-spacing": "0.04em",
            "pointer-events": "none",
          }}
        >
          connecting…
        </div>
      </Show>
    </div>
  );
};
