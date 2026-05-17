import { Component, createSignal, onCleanup, onMount } from "solid-js";

interface Props {
  intensity: () => number; // 0..1, drives the blob's pulse
  color: () => string;
}

export const StateBlob: Component<Props> = (props) => {
  const [pulse, setPulse] = createSignal(0);
  let frame: number | undefined;

  onMount(() => {
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      // First-order envelope toward target
      const target = props.intensity();
      setPulse((p) => p + (target - p) * Math.min(1, dt * 8));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  });
  onCleanup(() => frame && cancelAnimationFrame(frame));

  const scale = () => 0.6 + pulse() * 0.6;
  const opacity = () => 0.4 + pulse() * 0.5;

  return (
    <div
      class="absolute inset-0 flex items-center justify-center pointer-events-none"
      style={{ transition: "background 0.6s ease" }}
    >
      <div
        class="rounded-full"
        style={{
          width: "50vmin",
          height: "50vmin",
          background: `radial-gradient(circle, ${props.color()} 0%, transparent 70%)`,
          transform: `scale(${scale()})`,
          opacity: opacity().toString(),
          transition: "transform 30ms linear, opacity 30ms linear, background 0.6s ease",
        }}
      />
    </div>
  );
};
