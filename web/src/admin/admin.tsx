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
      <header class="flex items-center justify-between mb-4 pb-3 border-b border-slate-800">
        <h1 class="text-2xl font-bold">
          Lafufu <span class="text-slate-500 font-normal text-base ml-2">admin</span>
        </h1>
        <span class="text-xs text-slate-500 font-mono">v0.1.0 · Phase 0</span>
      </header>

      {/*
        Two-column flex layout: each column packs panels at their natural
        height, no row-alignment constraints. Below the columns: chat + pulse
        get full width since they're the wider/taller content.

        LEFT  | RIGHT
        ──────┼──────
        srv   | pose
        expr  | sliders
        ──────┴──────
              chat
            settings
              pulse
      */}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div class="flex flex-col gap-4">
          <ServiceStatus nats={nats} />
          <ExpressionButtons />
        </div>
        <div class="flex flex-col gap-4">
          <PoseView nats={nats} />
          <ServoSliders />
        </div>
      </div>

      <div class="flex flex-col gap-4">
        <ChatLog nats={nats} />
        <SettingsForm />
        <SystemPulse nats={nats} />
      </div>
    </div>
  );
};

export default Admin;
