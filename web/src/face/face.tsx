import { Component, createSignal, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { emotionToColor } from "../shared/design";
import { StateBlob } from "./state_blob";
import { Caption } from "./caption";

const Face: Component = () => {
  const [state, setState] = createSignal<string>("idle");
  const [emotion, setEmotion] = createSignal<string>("neutral");
  const [rms, setRms] = createSignal<number>(0);
  const [caption, setCaption] = createSignal<string | undefined>();

  const nats = new NatsWs();

  onMount(() => {
    nats.start();
    nats.subscribe("agent.state.*", (f) => {
      const tail = f.topic.split(".").pop();
      if (tail) setState(tail);
      // Auto-dim RMS on state changes
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
  });
  onCleanup(() => nats.stop());

  // Background gradient color from emotion
  const bgColor = () => emotionToColor(emotion());

  return (
    <div
      class="relative h-full w-full overflow-hidden"
      style={{
        background: `radial-gradient(ellipse at center, ${bgColor()}33 0%, #0f172a 70%, #020617 100%)`,
        transition: "background 0.6s ease",
      }}
    >
      <StateBlob intensity={rms} color={bgColor} />
      <div class="absolute top-10 left-0 right-0 text-center text-sm uppercase tracking-widest text-slate-300/60">
        {state()}
      </div>
      <Caption text={caption} />
    </div>
  );
};

export default Face;
