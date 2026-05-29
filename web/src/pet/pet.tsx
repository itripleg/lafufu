import { Component, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { PetDevice } from "./pet_device";

/**
 * /pet — full-screen Tamagotchi-lite. Just a backdrop around the reusable
 * PetDevice (the same widget the chat view embeds). Owns its own NATS link.
 */
const Pet: Component = () => {
  const nats = new NatsWs();
  onMount(() => nats.start());
  onCleanup(() => nats.stop());

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background:
          "radial-gradient(circle at 50% 30%, #2d2018 0%, #1a1410 60%, #0c0907 100%)",
        overflow: "hidden",
        "touch-action": "none",
      }}
    >
      <PetDevice nats={nats} />
    </div>
  );
};

export default Pet;
