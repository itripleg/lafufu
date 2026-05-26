# Idle Bootstrap — animator readiness handshake — design

> **Status:** design · **Date:** 2026-05-26

## Overview

On a cold boot the Pi starts all `lafufu-*` services nearly simultaneously. Control's `on_startup` calls `_publish_idle_expression()` on NATS — a one-shot publish — before the animator has finished connecting and subscribing to `animator.intent.play_expression`. NATS core pub/sub is fire-and-forget, so the message arrives at the broker before the animator's `subscribe_model` call has run. The publish is silently dropped, the animator's `_idle_payload` stays `None`, and Lafufu sits motionless at hardware power-up position until something else fires an expression intent.

This is a recurring class of cold-boot bug — race between "publisher ready" and "subscriber ready" — that systemd's `After=` ordering does not actually solve because every service uses `Type=simple`, which considers the process "started" the moment `ExecStart` forks, not when it has actually subscribed.

The post-merge hardening pass (Batch C of the 2026-05-26 sweep) inverted the systemd ordering so agent + animator now start after control — but that pulls the race in the opposite direction (it fixes the config-snapshot-request race the agent/animator have against control, at the cost of making the idle-publish race worse, since the animator now definitely starts AFTER control's idle publish).

This spec proposes the smallest design change that closes the idle gap reliably without rolling out `sd_notify` or migrating to JetStream.

## Goals

- **Lafufu is in idle motion within ~5 seconds of `lafufu.target` becoming active**, every cold boot, with no operator intervention.
- **No new infra** — same NATS core, same `Type=simple` systemd units. The fix lives in application code.
- **Symmetric**: control's `_rebroadcast_all_settings`, which has the same race shape (publishes before agent/animator subscribers exist), is fixed by the same mechanism.
- **Operator-visible** — when the handshake completes, a log line in the control journal makes that obvious. When the handshake never completes (animator never asks), a warning fires and the operator knows.

## Non-goals (this build)

- No `sd_notify`. That's the systemd-native answer and is captured in the May 20 review's observability theme; this spec does not move there.
- No NATS JetStream. JetStream persists messages so the animator could fetch the idle payload retroactively. The dependency is heavier than needed for one bootstrap message.
- No periodic re-publish of the idle expression as a heartbeat. That would mask the bug rather than fix it.
- No change to the existing `animator.intent.play_expression` subscribe path. The animator's normal playback flow is unaffected.

## Background — current state

- **`packages/control/src/lafufu_control/service.py:35-44`** — `_publish_idle_expression` does a one-shot `publish_model(...)` and returns. No retry, no ack.
- **`packages/control/src/lafufu_control/service.py:139`** — called once in `on_startup`.
- **`packages/animator/src/lafufu_animator/service.py:147`** (approx) — animator subscribes to `topics.ANIMATOR_INTENT_PLAY_EXPRESSION`. If the control publish lands before this subscribe completes, the message is gone.
- **`packages/animator/src/lafufu_animator/service.py:77`** — `_idle_payload: AnimatorIntentPlayExpression | None = None`. Once set, the keyframe player loop's idle fallback branch uses it.
- **`deploy/systemd/`** — every service is `Type=simple`. `After=` orders the startup launch but provides no readiness guarantee.

## Design — request-reply handshake on animator startup

The animator pulls the idle payload from control after its own subscriptions are active. Control no longer fires-and-forgets.

### New NATS topic — `animator.request.idle`

The animator publishes a request on this topic immediately after its `subscribe_model("animator.intent.play_expression", ...)` returns. Payload is empty (`{}`); the topic name carries all the semantics.

Control subscribes to `animator.request.idle` during `on_startup`. On receipt, it does what `_publish_idle_expression` currently does — looks up the idle expression in the DB, compiles it, publishes `animator.intent.play_expression`. Same compile path, same publish topic, just gated by request.

### Animator side

`_idle_payload` defaults to `None` as today. Once the animator finishes its initial NATS subscriptions, it publishes `animator.request.idle`. If `_idle_payload` is still `None` after a short window (say 2 s), publish the request again, up to 3 retries. After the third unsuccessful attempt, log a warning — operator now knows control isn't responding — and continue with idle disabled (the existing behavior today, but visible).

```python
# pseudocode, inside animator service.py after subscriptions:
async def _request_idle_payload(self) -> None:
    for attempt in range(3):
        await self.nats.publish(topics.ANIMATOR_REQUEST_IDLE, b"{}")
        # _on_play_expression fills _idle_payload as a side effect when a
        # play_expression with playback="random_walk" arrives.
        for _ in range(20):  # 20 × 100 ms = 2 s
            if self._idle_payload is not None:
                self.log.info("animator.idle.received attempt=%d", attempt + 1)
                return
            await asyncio.sleep(0.1)
    self.log.warning(
        "animator.idle.unanswered — control never responded to request.idle; "
        "idle animation disabled until next play_expression"
    )
```

The 100 ms × 20 polling is intentionally crude; the `_on_play_expression` handler that sets `_idle_payload` runs on the asyncio loop, so a condition-variable + `asyncio.Event.wait(timeout=2)` is cleaner. Keep the implementation simple to start; promote to an Event if the retry loop ends up doing more.

### Control side

`_publish_idle_expression` is **kept** as a helper but only called from the request handler, not from `on_startup`. The startup call is removed (no more one-shot fire-and-forget).

```python
# in ControlService.on_startup, after engine + DB setup, before the main NATS
# subscribe block:
async def on_request_idle(subject: str, msg) -> None:  # msg ignored
    self.log.info("animator.request.idle received — publishing idle expression")
    await _publish_idle_expression(engine, self.nats)

await self.nats.subscribe(topics.ANIMATOR_REQUEST_IDLE, cb=on_request_idle)
```

This also handles the **animator-restarted-mid-session** case for free: if the animator crashes and systemd restarts it, it re-requests on subscription completion and control re-publishes. The current code requires a control restart (or a manual `play_expression`) to recover.

### Topic name in `topics.py`

Add `ANIMATOR_REQUEST_IDLE = "animator.request.idle"` to `packages/shared/src/lafufu_shared/topics.py`. Used by both sides.

## Same pattern for `config_snapshot_request`

The existing `BaseService.request_config_snapshot()` (the May 20 review's other config-race finding) follows the same fire-and-forget shape against `_rebroadcast_all_settings`. After this spec lands, the request-reply handshake pattern is established and `_rebroadcast_all_settings` can adopt it identically:

- Service publishes `config.request.snapshot` after its own startup.
- Control subscribes, rebroadcasts all settings on receipt.
- Service either receives the snapshot (success) or times out and logs.

The systemd ordering fix in Batch C (animator/agent After=control) covers the snapshot race for now. The above is a follow-up if we want a robust, restart-tolerant solution.

## Migration order

Each step is independently mergeable; idle behavior is recovered cumulatively.

1. **Topic constant** — add `ANIMATOR_REQUEST_IDLE` to `topics.py`. Diff: 1 line. No test.
2. **Control subscriber** — control subscribes to `animator.request.idle` and re-publishes idle on receipt. The existing `_publish_idle_expression(...)` call inside `on_startup` stays for one release (defense in depth: animator missed the publish AND didn't ask → control's startup publish still fires).
3. **Animator requester** — after subscription, animator publishes the request and polls for the idle payload. New module-level helper `_request_idle_payload`.
4. **Drop control's startup publish** — once step 3 is field-validated, remove the `_publish_idle_expression(engine, self.nats)` call from `on_startup`. Idle is now handshake-only.
5. **Tests:**
   - Unit test for control subscriber: publish `animator.request.idle`, assert `animator.intent.play_expression` is published with `name="idle"`.
   - Animator integration test (with fake NATS bus): assert `animator.request.idle` is published after subscribe; assert `_idle_payload` is set when the corresponding play_expression arrives; assert retry + final warning if no response.

## Testing strategy

- **Cold-boot smoke** (manual or scripted): boot the Pi via `systemctl start lafufu.target`, watch `journalctl -fu lafufu-control -u lafufu-animator`. Within 5 s expect:
  - Animator: `animator.idle.received attempt=N` (N=1 for healthy boots, 2-3 for slow control startup).
  - Control: `animator.request.idle received — publishing idle expression`.
  - Lafufu's head visibly enters idle random-walk motion.
- **Animator restart while control is up** (kill animator's pid, wait for systemd `Restart=`). Animator re-handshakes; control re-publishes; idle resumes within 5 s.
- **Control restart while animator is up** (kill control's pid). Animator's idle is already set from the original handshake and continues unchanged. New `animator.request.idle` publishes will go unanswered until control's subscriber comes up — but since the animator already HAS idle, there's no observable regression.

## Risks & open questions

- **Animator boots without control ever coming up.** The 3-retry, 6-second timeout puts a floor on user wait, then logs and continues without idle. This is the same end state as today, just visible.
- **Race during animator's own subscribe.** If `_on_play_expression` arrives before `_idle_payload` is initialized (it isn't — `None` default), the handler should special-case `playback == "random_walk"` to populate the payload. Verify the existing handler already does this; if not, add the side effect.
- **Topic naming.** `animator.request.idle` vs `animator.intent.request_idle` vs `system.request.idle`. The `animator.*` namespace keeps the request close to the producer. `request.idle` is intentionally distinct from `intent.*` (which the animator publishes for itself to consume) to avoid loop confusion.
- **Multiple animators.** Out of scope. If we ever have multiple animator processes (we don't), each would request independently and control would re-publish; no coordination needed because the publish is broadcast.
- **Pre-shipped DBs without `idle` row.** `_publish_idle_expression` already handles this — returns silently. The request handler should NOT log a warning when no idle exists (that's a different "DB unseeded" condition, not an idle-bootstrap race).

## Out of scope

- `sd_notify` / `Type=notify` rollout for all services. Tracked separately in the May 20 review's observability theme.
- JetStream / message persistence. Too heavy for one bootstrap message.
- `WatchdogSec` on the units. Tracked separately.
- Authentication on the new topic. Inherits the existing NATS-bus-is-open posture. Whatever the auth solution is for the bus, this topic is covered.
