import { Component, createSignal, onCleanup, onMount, Show, For } from "solid-js";
import * as THREE from "three";
import { NatsWs } from "../shared/nats_ws";
import { api } from "../shared/api";
import { emotionToColor, EMOTION_GLYPH, type Emotion } from "../shared/design";
import { toast } from "../shared/toast";
import { Blob } from "../shared/blob";
import { createPetScene, SERVO_RANGES } from "./pet_scene";
import { applyDragDelta, axisMid } from "./head_drag";

type ChatLine = { who: "you" | "lafufu"; text: string; emotion?: string; ts: number };

/** Settings carry bools as strings ("true"/"1"); NATS config events may carry
 *  a real boolean. Normalize both. */
const parseBool = (v: unknown): boolean =>
  v === true || v === "true" || v === "1" || v === 1;

/**
 * Mobile Tamagotchi-style page — three.js procedural labubu face that mirrors
 * the live servo pose, plus easter-egg interactions (pat / tug / poke /
 * shake) that fire animator expressions. CSS2DRenderer-equivalent overlays
 * are HTML positioned via the camera projection.
 */
const Pet: Component = () => {
  const [state, setState] = createSignal<string>("idle");
  const [emotion, setEmotion] = createSignal<Emotion>("neutral");
  const [caption, setCaption] = createSignal<string | undefined>();
  const [hint, setHint] = createSignal<{ x: number; y: number; text: string; id: number } | null>(null);
  const [chat, setChat] = createSignal<ChatLine[]>([]);
  const [showChat, setShowChat] = createSignal(false);
  const [chatInput, setChatInput] = createSignal("");
  const [sending, setSending] = createSignal(false);
  const [discovered, setDiscovered] = createSignal<Set<string>>(new Set());
  // Mirrors the animator.idle_animation.enabled setting. Defaults to true (the
  // setting's factory default); corrected on mount + by config.changed events.
  const [idleOn, setIdleOn] = createSignal(true);

  const nats = new NatsWs();
  let host!: HTMLDivElement;
  let api3d: ReturnType<typeof createPetScene> | undefined;

  const EASTER_EGGS = [
    { id: "pat",     label: "Pat the head"     },
    { id: "tugL",    label: "Tug an ear"        },
    { id: "tugR",    label: "Tug the other ear" },
    { id: "poke",    label: "Poke the mouth"    },
    { id: "shake",   label: "Shake the device"  },
    { id: "spin",    label: "Spin it around"    },
  ];

  const flashHint = (x: number, y: number, text: string) => {
    const id = Date.now();
    setHint({ x, y, text, id });
    window.setTimeout(() => setHint((h) => (h?.id === id ? null : h)), 1600);
  };

  const markFound = (id: string) => {
    setDiscovered((d) => {
      if (d.has(id)) return d;
      const n = new Set(d); n.add(id);
      toast.ok(`easter egg unlocked: ${id}`, `${n.size} / ${EASTER_EGGS.length} found`);
      return n;
    });
  };

  const triggerExpression = async (name: string) => {
    try {
      await api.animatorExpression(name);
      setEmotion(name as Emotion);
      api3d?.setEmotion(name);
    } catch {
      // Silent — the visual will still snap locally and toast will surface a hint.
      api3d?.setEmotion(name);
      setEmotion(name as Emotion);
    }
  };

  // ---- Pointer interaction: drag puppeteers the head; tap detects zones ----
  let dragging = false;
  let didDrag = false;
  let gesture: "none" | "puppeteer" | "tug" = "none";
  let tugSide: "L" | "R" | null = null;
  let lastX = 0, lastY = 0;
  let downX = 0, downY = 0;
  let downT = 0;
  let velY = 0;            // last drag dy — used to detect downward "tug" gestures

  // Commanded head-servo targets (DXL units) while puppeteering.
  let headLr = axisMid("head_lr");
  let headUd = axisMid("head_ud");
  let lastHeadDragTs = 0;                       // post-release grace window
  let lastPose: Record<string, number> = {};   // latest animator.pose payload

  // Spin easter-egg: timestamps of recent left<->right reversals near a
  // range extreme. Replaces the old unbounded-yaw detector.
  let sweepReversals: number[] = [];
  let sweepDir = 0;        // -1 / +1 — last horizontal drag direction

  // True while the user owns the head: actively puppeteering, or within 800ms
  // of release. The animator.pose echo is suppressed for head_lr/head_ud
  // during this window so the servo round-trip can't fight the drag.
  const headControlActive = () =>
    gesture === "puppeteer" || performance.now() - lastHeadDragTs < 800;

  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  const updateRaycaster = (clientX: number, clientY: number) => {
    const rect = host.getBoundingClientRect();
    ndc.x =  ((clientX - rect.left) / rect.width)  * 2 - 1;
    ndc.y = -((clientY - rect.top)  / rect.height) * 2 + 1;
    if (api3d) raycaster.setFromCamera(ndc, api3d.camera);
  };

  const pickZone = (clientX: number, clientY: number): string | null => {
    if (!api3d) return null;
    updateRaycaster(clientX, clientY);
    const hits = raycaster.intersectObjects(api3d.hitGroup.children, false);
    return hits.length ? (hits[0].object.userData.zone as string) : null;
  };

  // Throttled servo command — sends the latest headLr/headUd at most ~every
  // 40ms. A throttle (not body_panel's trailing debounce) so the servos keep
  // tracking during a continuous drag, not only when the finger pauses.
  let previewTimer: ReturnType<typeof setTimeout> | undefined;
  const flushPreview = () => {
    previewTimer = undefined;
    api.animatorPreview("head_lr", Math.round(headLr)).catch(() => {});
    api.animatorPreview("head_ud", Math.round(headUd)).catch(() => {});
  };
  const schedulePreview = () => {
    if (previewTimer === undefined) previewTimer = setTimeout(flushPreview, 40);
  };

  // Count a reversal when the horizontal drag flips direction while the head
  // is near a head_lr extreme. 3 within 1.5s trips the "spin" easter egg.
  const trackSweep = (dx: number) => {
    if (Math.abs(dx) < 2) return;
    const dir = dx > 0 ? 1 : -1;
    const [lo, hi] = SERVO_RANGES.head_lr;
    const span = hi - lo;
    const nearEnd = headLr <= lo + span * 0.12 || headLr >= hi - span * 0.12;
    if (sweepDir !== 0 && dir !== sweepDir && nearEnd) {
      const now = performance.now();
      sweepReversals = sweepReversals.filter((t) => now - t < 1500);
      sweepReversals.push(now);
    }
    sweepDir = dir;
  };

  const onPointerDown = (e: PointerEvent) => {
    dragging = true;
    didDrag = false;
    lastX = e.clientX; lastY = e.clientY;
    downX = e.clientX; downY = e.clientY; downT = performance.now();
    velY = 0;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);

    // The zone under the initial press decides the gesture for this drag.
    const zone = pickZone(e.clientX, e.clientY);
    if (zone === "earL" || zone === "earR") {
      gesture = "tug";
      tugSide = zone === "earL" ? "L" : "R";
    } else {
      gesture = "puppeteer";
      tugSide = null;
      // Grab the head wherever it currently is.
      headLr = lastPose.head_lr ?? axisMid("head_lr");
      headUd = lastPose.head_ud ?? axisMid("head_ud");
      sweepReversals = [];
      sweepDir = 0;
    }
  };
  const onPointerMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) didDrag = true;
    lastX = e.clientX; lastY = e.clientY;
    velY = dy;

    if (gesture === "puppeteer") {
      // Drag right -> head turns right; drag down -> head tilts down. If the
      // rig moves the wrong way during manual verification, negate dx/dy here.
      headLr = applyDragDelta("head_lr", headLr, dx);
      headUd = applyDragDelta("head_ud", headUd, dy);
      lastHeadDragTs = performance.now();
      // Optimistic visual update — the model tracks the drag immediately.
      api3d?.setPose({ head_lr: headLr, head_ud: headUd });
      schedulePreview();
      trackSweep(dx);
    }
  };
  const onPointerUp = (e: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    const heldMs = performance.now() - downT;
    const wasGesture = gesture;
    gesture = "none";

    if (!didDrag && heldMs < 350) {
      // Tap — check zone (pat / poke / tickle).
      const zone = pickZone(e.clientX, e.clientY);
      if (zone) handleTap(zone, e.clientX, e.clientY);
      return;
    }

    if (wasGesture === "tug") {
      // Drag that started on an ear, flicked downward — "tug" easter egg.
      if (tugSide && velY > 6) {
        markFound(tugSide === "L" ? "tugL" : "tugR");
        api3d?.wobbleEar(tugSide);
        triggerExpression("surprised");
        flashHint(e.clientX, e.clientY, "boi-oing!");
      }
    } else if (wasGesture === "puppeteer") {
      // Keep the 800ms grace window alive past release so the animator.pose
      // echo can't reclaim the head before the servo settles.
      lastHeadDragTs = performance.now();
      // Commit the exact release position promptly.
      if (previewTimer !== undefined) {
        clearTimeout(previewTimer);
        previewTimer = undefined;
      }
      flushPreview();
      // "Spin" easter egg — 3 rapid end-to-end reversals.
      if (sweepReversals.length >= 3) {
        sweepReversals = [];
        markFound("spin");
        triggerExpression("disagree");
        flashHint(e.clientX, e.clientY, "stop spinning me!");
      }
    }
  };

  // ---- Pat detector: 3 quick taps on the head zone ------------------------
  let patWindow: number[] = [];
  const handleTap = (zone: string, x: number, y: number) => {
    api3d?.bumpScale();
    if (zone === "head") {
      const now = performance.now();
      patWindow = patWindow.filter((t) => now - t < 1200);
      patWindow.push(now);
      if (patWindow.length >= 3) {
        patWindow = [];
        markFound("pat");
        api3d?.squashOnPat();
        triggerExpression("happy");
        flashHint(x, y, "yay! :)");
      } else {
        flashHint(x, y, "more!");
      }
    }
    if (zone === "earL" || zone === "earR") {
      api3d?.wobbleEar(zone === "earL" ? "L" : "R");
      flashHint(x, y, "tickles");
    }
    if (zone === "mouth") {
      markFound("poke");
      triggerExpression("disagree");
      flashHint(x, y, "hey!");
    }
  };

  // ---- Device-motion shake detector ---------------------------------------
  let lastShakeMag = 0, shakeAcc = 0;
  const onMotion = (e: DeviceMotionEvent) => {
    const a = e.accelerationIncludingGravity;
    if (!a) return;
    const mag = Math.hypot(a.x ?? 0, a.y ?? 0, a.z ?? 0);
    const delta = Math.abs(mag - lastShakeMag);
    lastShakeMag = mag;
    shakeAcc = shakeAcc * 0.92 + delta;
    if (shakeAcc > 35) {
      shakeAcc = 0;
      markFound("shake");
      triggerExpression("angry");
      flashHint(window.innerWidth / 2, 80, "stop shaking!");
    }
  };

  onMount(() => {
    api3d = createPetScene(host);

    nats.start();
    // Collect every unsub so onCleanup drains the listener map.
    const subs: Array<() => void> = [];
    subs.push(nats.subscribe("agent.state.*", (f) => {
      const tail = f.topic.split(".").pop();
      if (tail) setState(tail);
    }));
    subs.push(nats.subscribe("agent.reply", (f) => {
      const em = (f.payload.emotion ?? "neutral") as Emotion;
      setEmotion(em);
      api3d?.setEmotion(em);
      setCaption(f.payload.text);
      setChat((c) => [...c.slice(-30), { who: "lafufu", text: f.payload.text, emotion: em, ts: Date.now() }]);
    }));
    subs.push(nats.subscribe("agent.transcript", (f) => {
      setCaption(f.payload.text);
      setChat((c) => [...c.slice(-30), { who: "you", text: f.payload.text, ts: Date.now() }]);
    }));
    subs.push(nats.subscribe("animator.pose", (f) => {
      lastPose = f.payload;
      // While the user owns the head, drop the head axes from the echo so the
      // servo round-trip can't fight the drag. Eyes/jaw/brow keep flowing.
      const p = { ...f.payload };
      if (headControlActive()) {
        delete p.head_lr;
        delete p.head_ud;
      }
      api3d?.setPose(p);
    }));
    subs.push(nats.subscribe(
      "config.changed.animator.idle_animation.enabled",
      (f) => setIdleOn(parseBool(f.payload?.value)),
    ));
    onCleanup(() => subs.forEach((u) => u()));

    // Seed the idle toggle from the current server state.
    api.snapshot()
      .then((snap) => {
        const row = snap.settings.find(
          (s) => s.key === "animator.idle_animation.enabled",
        );
        if (row) setIdleOn(parseBool(row.value));
      })
      .catch(() => { /* keep the default-on state */ });

    // iOS requires explicit permission for devicemotion. Wire it up either way.
    window.addEventListener("devicemotion", onMotion, { passive: true });

    onCleanup(() => {
      if (previewTimer !== undefined) clearTimeout(previewTimer);
      window.removeEventListener("devicemotion", onMotion);
    });
  });

  onCleanup(() => {
    api3d?.dispose();
    nats.stop();
  });

  const sendChat = async () => {
    const text = chatInput().trim();
    if (!text || sending()) return;
    setSending(true);
    setChatInput("");
    setChat((c) => [...c.slice(-30), { who: "you", text, ts: Date.now() }]);
    try {
      await api.agentTextMessage(text);
    } catch (e: any) {
      toast.err("couldn't reach lafufu", e.message);
    } finally {
      setSending(false);
    }
  };

  const requestMotion = async () => {
    // iOS 13+: must call DeviceMotionEvent.requestPermission from a user gesture.
    const anyDM: any = (window as any).DeviceMotionEvent;
    if (anyDM && typeof anyDM.requestPermission === "function") {
      try {
        const res = await anyDM.requestPermission();
        if (res === "granted") toast.ok("motion enabled", "now try shaking 🪄");
        else toast.warn("motion denied");
      } catch (e: any) { toast.err("motion error", e.message); }
    } else {
      toast.info("motion already active on this device");
    }
  };

  const toggleIdle = async () => {
    const next = !idleOn();
    setIdleOn(next); // optimistic
    try {
      await api.patchSetting("animator.idle_animation.enabled", {
        value: next,
        value_type: "bool",
      });
    } catch (e: any) {
      setIdleOn(!next); // revert
      toast.err("couldn't toggle idle animation", e.message);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "radial-gradient(circle at 50% 30%, #2d2018 0%, #1a1410 60%, #0c0907 100%)",
        overflow: "hidden",
        "touch-action": "none",
      }}
    >
      {/* Atmospheric blobs */}
      <Blob size="55vmin" color={emotionToColor(emotion())} opacity={0.32} blur={70}
            style={{ top: "-12vmin", left: "-12vmin" }} drift delay={0} />
      <Blob size="40vmin" color="var(--c-mauve)" opacity={0.18} blur={60} variant={2}
            style={{ bottom: "-10vmin", right: "-10vmin" }} drift delay={6} />

      {/* Top status bar */}
      <div
        style={{
          position: "absolute",
          top: "max(14px, env(safe-area-inset-top))",
          left: "16px", right: "16px",
          display: "flex",
          "justify-content": "space-between",
          "align-items": "center",
          "z-index": 3,
        }}
      >
        <div class="eyebrow" style={{ color: emotionToColor(emotion()) }}>
          {state()}
        </div>
        <div
          style={{
            display: "flex",
            gap: "8px",
            "align-items": "center",
            "font-family": "var(--f-mono)",
            "font-size": "11px",
            color: "var(--c-mist)",
          }}
        >
          <span>{discovered().size}/{EASTER_EGGS.length}</span>
          <span style={{ opacity: .4 }}>·</span>
          <a
            href="/admin"
            style={{ color: "var(--c-mist)", "text-decoration": "none", opacity: .7 }}
          >
            admin →
          </a>
        </div>
      </div>

      {/* Three.js canvas host */}
      <div
        ref={host}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        style={{
          position: "absolute",
          inset: 0,
          "z-index": 2,
        }}
      />

      {/* Floating hint bubble — anchored to interaction point */}
      <Show when={hint()}>
        <div
          style={{
            position: "absolute",
            left: `${hint()!.x}px`,
            top:  `${hint()!.y - 60}px`,
            transform: "translate(-50%, -100%)",
            "padding": "8px 14px",
            "border-radius": "999px",
            "background": "rgba(243, 236, 220, 0.95)",
            "color": "#1a1410",
            "font-family": "var(--f-display)",
            "font-style": "italic",
            "font-size": "16px",
            "box-shadow": "0 8px 24px rgba(0,0,0,.4)",
            "z-index": 5,
            "pointer-events": "none",
            animation: "fade-up .4s cubic-bezier(.2,.7,.3,1.1) both",
            "white-space": "nowrap",
          }}
        >
          {hint()!.text}
        </div>
      </Show>

      {/* Bottom drawer: caption + chat toggle + easter-egg checklist */}
      <div
        style={{
          position: "absolute",
          bottom: 0, left: 0, right: 0,
          padding: "20px 16px max(20px, env(safe-area-inset-bottom)) 16px",
          "z-index": 3,
          background: "linear-gradient(to top, rgba(12,9,7,0.85) 0%, rgba(12,9,7,0) 100%)",
          "pointer-events": "none",
        }}
      >
        <Show when={caption() && !showChat()}>
          <div
            style={{
              "max-width": "560px",
              margin: "0 auto 14px",
              "padding": "10px 16px",
              "border-radius": "16px",
              background: "rgba(243, 236, 220, 0.07)",
              "backdrop-filter": "blur(10px)",
              "-webkit-backdrop-filter": "blur(10px)",
              border: "1px solid rgba(243, 236, 220, .08)",
              color: "var(--c-bone)",
              "font-family": "var(--f-display)",
              "font-style": "italic",
              "font-size": "16px",
              "line-height": 1.35,
              "text-align": "center",
              "pointer-events": "auto",
              animation: "fade-up .4s cubic-bezier(.2,.7,.3,1.1) both",
            }}
          >
            {caption()}
          </div>
        </Show>

        <Show when={showChat()}>
          <div
            style={{
              "max-width": "560px",
              margin: "0 auto 12px",
              background: "rgba(15, 11, 8, 0.85)",
              "backdrop-filter": "blur(14px)",
              "-webkit-backdrop-filter": "blur(14px)",
              "border-radius": "20px",
              border: "1px solid rgba(243, 236, 220, .1)",
              padding: "14px",
              "max-height": "44vh",
              display: "flex",
              "flex-direction": "column",
              "pointer-events": "auto",
            }}
          >
            <div
              class="scroll-warm"
              style={{
                flex: 1,
                "overflow-y": "auto",
                "padding-right": "6px",
                "margin-bottom": "8px",
                display: "flex",
                "flex-direction": "column",
                gap: "8px",
              }}
            >
              <For each={chat()}>
                {(line) => (
                  <div
                    style={{
                      "align-self": line.who === "you" ? "flex-end" : "flex-start",
                      "max-width": "82%",
                      padding: "8px 12px",
                      "border-radius": line.who === "you" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
                      background: line.who === "you" ? "var(--c-raised)" : "rgba(149,176,122,0.15)",
                      color: "var(--c-bone)",
                      "font-size": "14px",
                      "line-height": 1.35,
                      "border": "1px solid var(--c-edge)",
                    }}
                  >
                    <Show when={line.who === "lafufu" && line.emotion}>
                      <span
                        class="f-mono"
                        style={{
                          color: emotionToColor(line.emotion),
                          "font-size": "10px",
                          "margin-right": "6px",
                          opacity: .85,
                        }}
                      >
                        {EMOTION_GLYPH[(line.emotion ?? "neutral") as Emotion]} {line.emotion}
                      </span>
                    </Show>
                    {line.text}
                  </div>
                )}
              </For>
              <Show when={chat().length === 0}>
                <div
                  style={{
                    color: "var(--c-stone)",
                    "font-size": "13px",
                    "text-align": "center",
                    padding: "20px 8px",
                    "font-family": "var(--f-display)",
                    "font-style": "italic",
                  }}
                >
                  say hi to lafufu...
                </div>
              </Show>
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <input
                class="field"
                style={{ flex: 1 }}
                placeholder="type to lafufu..."
                value={chatInput()}
                disabled={sending()}
                onInput={(e) => setChatInput(e.currentTarget.value)}
                onKeyDown={(e) => e.key === "Enter" && sendChat()}
              />
              <button class="btn btn--primary" onClick={sendChat} disabled={sending()}>
                send
              </button>
            </div>
          </div>
        </Show>

        {/* Action chips row */}
        <div
          style={{
            display: "flex",
            "justify-content": "center",
            gap: "10px",
            "flex-wrap": "wrap",
            "pointer-events": "auto",
          }}
        >
          <button
            class={`btn btn--tiny ${showChat() ? "btn--primary" : ""}`}
            onClick={() => setShowChat((v) => !v)}
          >
            {showChat() ? "hide chat" : "open chat"}
          </button>
          <button class="btn btn--tiny" onClick={requestMotion}>
            enable shake
          </button>
          <button
            class={`btn btn--tiny ${idleOn() ? "btn--primary" : ""}`}
            onClick={toggleIdle}
            title="Toggle the lafufu's idle 'living presence' animation. Off = the head holds where you drag it."
          >
            {idleOn() ? "idle: on" : "idle: off"}
          </button>
          <button
            class="btn btn--tiny"
            onClick={() => {
              const found = discovered();
              const remaining = EASTER_EGGS.filter((e) => !found.has(e.id));
              const next = remaining[0] ?? EASTER_EGGS[0];
              toast.info(`hint: ${next.label}`,
                `${found.size}/${EASTER_EGGS.length} unlocked`);
            }}
          >
            hint
          </button>
        </div>
      </div>
    </div>
  );
};

export default Pet;
