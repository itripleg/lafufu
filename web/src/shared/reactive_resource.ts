import { createResource, onCleanup } from "solid-js";
import type { NatsWs } from "./nats_ws";

/**
 * Solid resource that refetches when any of the listed NATS topics fires.
 *
 * Use for list-views that should reactively follow backend state changes
 * (e.g. expressions, frames, servo config). Payloads are ignored — listeners
 * just refetch the full list.
 *
 * The caller passes the `NatsWs` instance because pages instantiate their own
 * (admin.tsx, face.tsx, pet.tsx each have their own); there is no global
 * singleton to import.
 *
 * Subscriptions are registered synchronously so they are active immediately
 * and cleaned up when the owner scope disposes.
 */
export function createReactiveResource<T>(
  fetchFn: () => Promise<T>,
  topics: string[],
  nats: NatsWs,
) {
  const [data, { refetch }] = createResource(fetchFn);
  const unsubs = topics.map((t) =>
    nats.subscribe(t, () => {
      void refetch();
    }),
  );
  onCleanup(() => unsubs.forEach((u) => u()));
  return data;
}
