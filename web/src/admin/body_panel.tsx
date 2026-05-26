import { Component } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { ExpressionsSection } from "./expressions_section";
import { FramesSection } from "./frames_section";

/** Body tab — animation authoring: frames and expressions. */
export const BodyPanel: Component<{ nats: NatsWs }> = (props) => {
  return (
    <div class="flex flex-col gap-6">
      <FramesSection nats={props.nats} />
      <ExpressionsSection nats={props.nats} />
    </div>
  );
};
