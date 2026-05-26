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
 *
 * Returns `[data, refetch]`. Callers that want immediate post-mutation
 * feedback should `await refetch()` after a successful API call — this
 * avoids the brief UI flash that happens when relying solely on the
 * NATS-driven async refetch (the publish may not land for hundreds of ms,
 * during which the UI shows stale data).
 */
export function createReactiveResource<T>(
  fetchFn: () => Promise<T>,
  topics: string[],
  nats: NatsWs,
) {
  const [data, { refetch }] = createResource(fetchFn);
  const onError = (err: unknown) => {
    // eslint-disable-next-line no-console
    console.warn("reactive_resource: refetch failed", err);
  };
  const unsubs = topics.map((t) =>
    nats.subscribe(t, () => {
      // NATS-driven refetch errors (e.g. backend bouncing) would otherwise
      // surface as unhandled promise rejections / dev-mode rethrows from
      // inside Solid's load wrapper. Catch both sync throws and async
      // rejections; the previous resource value is preserved by Solid.
      try {
        const r = refetch();
        if (r && typeof (r as Promise<unknown>).then === "function") {
          (r as Promise<unknown>).catch(onError);
        }
      } catch (err) {
        onError(err);
      }
      void t;
    }),
  );
  onCleanup(() => unsubs.forEach((u) => u()));
  return [data, refetch] as const;
}
