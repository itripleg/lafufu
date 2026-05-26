import { api } from "./api";
import type { NatsWs } from "./nats_ws";
import { createReactiveResource } from "./reactive_resource";

/** Servo config (ranges + idle defaults + operator overrides) from the
 * control plane. Refetches when any animator.<servo>.default setting changes.
 * Returns just the data accessor — callers don't need to trigger refetches
 * (the page receives them automatically via NATS). */
export function useServoConfig(nats: NatsWs) {
  const [data] = createReactiveResource(
    api.getAnimatorConfig,
    [
      "config.changed.animator.head_lr.default",
      "config.changed.animator.head_ud.default",
      "config.changed.animator.eye.default",
      "config.changed.animator.jaw.default",
      "config.changed.animator.brow.default",
    ],
    nats,
  );
  return data;
}
