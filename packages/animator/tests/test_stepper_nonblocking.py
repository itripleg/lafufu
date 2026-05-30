"""Regression test for the servo-twitch root cause (Mechanism B).

The animator's `_stepper_loop` calls `_move_to_pose()`, which issues five
SYNCHRONOUS Dynamixel round-trips (`bus.write` -> `write4ByteTxRx`) directly
on the asyncio event loop. Each round-trip blocks the *whole* loop until the
servo's status packet returns. On a loaded Pi (e.g. a Chromium renderer
pegging a core, or serial-bus contention from the debug testbed) those
round-trips stretch, the loop is starved, the stepper's wall-clock `dt`
becomes irregular, and `smooth_damp` turns the fat `dt` into a lurch —
visible as twitchy servos that get worse "after a while".

This test runs the real stepper against a bus whose writes are slow and asserts
the event loop stays responsive enough to service a 5 ms heartbeat. It pins the
fix: the stepper runs in a dedicated writer thread that owns the bus, so the
blocking serial round-trips never stall the loop. (Before the fix the loop was
stalled ~150 ms/tick.)
"""

import asyncio
import itertools
import time

from lafufu_animator.service import AnimatorService
from lafufu_shared.testing import FakeDxlBus

# Per-write serial latency to simulate. 5 servos/tick => one stepper tick
# blocks the loop for ~5 * PER_WRITE_S if writes run on the loop.
PER_WRITE_S = 0.03  # 30 ms; 5 servos => ~150 ms blocked per tick when on-loop

# The loop must never be stalled longer than this. A single on-loop write
# burst is ~150 ms; an off-loop implementation keeps gaps at the heartbeat
# cadence (~5-15 ms). 100 ms cleanly separates the two.
MAX_ACCEPTABLE_GAP_S = 0.10


class SlowDxlBus(FakeDxlBus):
    """FakeDxlBus whose writes block like a real (or contended) serial bus."""

    def write(self, name: str, position: int) -> None:
        time.sleep(PER_WRITE_S)  # synchronous, like write4ByteTxRx
        super().write(name, position)


async def test_stepper_does_not_block_event_loop():
    bus = SlowDxlBus()
    # Production stepper rate. No NATS needed — we start the stepper directly.
    svc = AnimatorService(bus=bus, nats_url="nats://unused:4222", stepper_hz=30.0)
    svc._has_u2d2 = True

    ticks: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        # A well-behaved coroutine that should get the loop every ~5 ms.
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    # The fix: the stepper runs in a dedicated writer thread that owns the bus,
    # so its blocking serial round-trips never stall the event loop.
    svc._start_stepper(asyncio.get_running_loop())
    hb = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(1.0)  # let several stepper ticks happen
    finally:
        stop.set()
        svc._stepper_stop.set()
        await asyncio.gather(hb, return_exceptions=True)
        svc._stepper_thread.join(timeout=2)

    # Sanity: the stepper actually ran and wrote servos.
    assert bus.writes, "stepper never wrote to the bus"
    assert len(ticks) >= 2, "heartbeat never ran"

    gaps = [b - a for a, b in itertools.pairwise(ticks)]
    max_gap = max(gaps)
    assert max_gap < MAX_ACCEPTABLE_GAP_S, (
        f"event loop was stalled for {max_gap * 1000:.0f} ms by servo writes "
        f"(limit {MAX_ACCEPTABLE_GAP_S * 1000:.0f} ms). Servo bus I/O is running "
        f"on the event loop and starving other coroutines — move it to a "
        f"dedicated writer thread/executor."
    )
