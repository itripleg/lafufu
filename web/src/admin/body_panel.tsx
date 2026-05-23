import { Component } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { FramesSection } from "./frames_section";

/** Body tab — animation authoring (frames, then expressions in Task 5.1).
 *
 * Replaces the old procedural expression-pill UI: expressions are no longer
 * fixed presets but user-editable keyframe animations backed by the Frame
 * + Expression DB tables on control. The animator runs a single
 * KeyframePlayer driving 20Hz pose updates from the active payload.
 */
export const BodyPanel: Component<{ nats: NatsWs }> = (props) => {
  return (
    <div class="flex flex-col gap-6">
      <FramesSection nats={props.nats} />
      {/* ExpressionsSection mounts in Task 5.1 */}
    </div>
  );
};
