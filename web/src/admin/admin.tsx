import { Component, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { ServiceStatus } from "./service_status";
import { SettingsForm } from "./settings_form";
import { PoseView } from "./pose_view";
import { ServoSliders } from "./servo_sliders";
import { ExpressionButtons } from "./expression_buttons";

const Admin: Component = () => {
  const nats = new NatsWs();
  onMount(() => nats.start());
  onCleanup(() => nats.stop());

  return (
    <div class="min-h-screen p-6 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      <header class="col-span-full flex items-center justify-between">
        <h1 class="text-2xl font-bold">Lafufu admin</h1>
        <span class="text-sm text-slate-400">v0.1.0 · Phase 0</span>
      </header>
      <ServiceStatus nats={nats} />
      <SettingsForm />
      <PoseView nats={nats} />
      <ServoSliders />
      <ExpressionButtons />
    </div>
  );
};

export default Admin;
