import { Component, onCleanup, onMount } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { ServiceStatus } from "./service_status";
import { SettingsForm } from "./settings_form";
import { PoseView } from "./pose_view";
import { ServoSliders } from "./servo_sliders";
import { ExpressionButtons } from "./expression_buttons";
import { ChatLog } from "./chat_log";
import { SystemPulse } from "./system_pulse";

const Admin: Component = () => {
  const nats = new NatsWs();
  onMount(() => nats.start());
  onCleanup(() => nats.stop());

  return (
    <div class="min-h-screen p-4 lg:p-6 max-w-[1600px] mx-auto">
      <header class="flex items-center justify-between mb-4">
        <h1 class="text-2xl font-bold">
          Lafufu <span class="text-slate-500 font-normal text-base ml-2">admin</span>
        </h1>
        <span class="text-xs text-slate-500 font-mono">v0.1.0 · Phase 0</span>
      </header>

      {/*
        Layout (lg+):
        ┌─────────────────────────┬───────────────────────────┐
        │ status rail (4 cols)    │ chat / interact (8 cols)  │
        │  - services             │  - chat panel (tabs)      │
        │  - live pose            │                           │
        │  - expressions          │                           │
        ├─────────────────────────┴───────────────────────────┤
        │ servo sliders (4 cols)  │ settings (8 cols)         │
        ├─────────────────────────────────────────────────────┤
        │ system pulse (full)                                 │
        └─────────────────────────────────────────────────────┘
      */}
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Status rail — at-a-glance overview */}
        <div class="lg:col-span-4 flex flex-col gap-4">
          <ServiceStatus nats={nats} />
          <PoseView nats={nats} />
          <ExpressionButtons />
        </div>

        {/* Primary interaction surface */}
        <div class="lg:col-span-8 flex flex-col gap-4">
          <ChatLog nats={nats} />
        </div>

        {/* Direct controls */}
        <div class="lg:col-span-4">
          <ServoSliders />
        </div>

        {/* Configuration */}
        <div class="lg:col-span-8">
          <SettingsForm />
        </div>

        {/* Debug / observability */}
        <div class="lg:col-span-12">
          <SystemPulse nats={nats} />
        </div>
      </div>
    </div>
  );
};

export default Admin;
