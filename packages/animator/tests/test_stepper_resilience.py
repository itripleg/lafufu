"""Regression tests for servo-bus fault tolerance + self-healing.

Root cause of a prod incident (2026-05-30): a SINGLE transient Dynamixel
comm timeout (`result=-3001`, COMM_RX_TIMEOUT — the servo missed one
round-trip) made `_move_to_pose` raise OSError, which the stepper treated
as fatal: it flipped `_has_u2d2 = False` and went degraded. Both the servo
writer and the idle-fallback spawn are gated on `_has_u2d2`, and nothing
ever set it back to True — so one transient timeout killed all motion and
idle until a manual service restart. `dmesg` showed no USB disconnect; the
bus was fine. Giving up the whole subsystem on the first transient is wrong.

These tests pin the fix:
  1. A single transient write failure must NOT degrade the bus.
  2. Sustained failures DO degrade (after a small consecutive threshold).
  3. A degraded bus auto-recovers by re-opening — no restart needed.
"""

import asyncio

from lafufu_animator.service import AnimatorService
from lafufu_shared.testing import FakeDxlBus

_RX_TIMEOUT = "DXL write head_ud: comm failed (result=-3001)"


class CountedFailBus(FakeDxlBus):
    """FakeDxlBus that fails its first ``fail_n`` write calls, then succeeds.

    Models transient serial timeouts: the OSError surfaces exactly like the
    real bus's ``_check`` raising on a non-success comm result.
    """

    def __init__(self, fail_n: int = 0, **kw) -> None:
        super().__init__(**kw)
        self.fail_n = fail_n

    def write(self, name: str, position: int) -> None:
        if self.fail_n > 0:
            self.fail_n -= 1
            raise OSError(_RX_TIMEOUT)
        super().write(name, position)


class RecoverableDxlBus(FakeDxlBus):
    """FakeDxlBus whose ``open()`` brings a dropped bus back, like the real one.

    The real DxlBus.open() re-probes /dev/ttyUSB* and re-attaches the port,
    raising ConnectionError when the device is absent. Here ``open()`` succeeds
    only while ``device_present`` is set — letting a test hold the bus down,
    confirm it stays degraded, then bring the device back and confirm recovery.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.open_calls = 0
        self.device_present = True

    def open(self) -> None:
        self.open_calls += 1
        if not self.device_present:
            raise ConnectionError("U2D2 not found")
        self._connected = True


async def _run_stepper_for(svc: AnimatorService, seconds: float) -> None:
    svc._start_stepper(asyncio.get_running_loop())
    try:
        await asyncio.sleep(seconds)
    finally:
        svc._stepper_stop.set()
        if svc._stepper_thread is not None:
            svc._stepper_thread.join(timeout=2)


async def test_single_transient_write_failure_does_not_degrade():
    """One -3001 timeout must be tolerated — not treated as a dead bus."""
    bus = CountedFailBus(fail_n=1)
    svc = AnimatorService(bus=bus, nats_url="nats://unused:4222", stepper_hz=50.0)
    svc._has_u2d2 = True

    await _run_stepper_for(svc, 0.3)

    assert svc._has_u2d2 is True, (
        "a single transient comm timeout must NOT permanently disable the bus"
    )
    assert bus.writes, "stepper must keep driving servos after a transient failure"


async def test_sustained_write_failures_degrade_after_threshold():
    """Persistent failures (real fault) must eventually degrade, not spin forever."""
    bus = CountedFailBus(fail_n=10_000)  # fail effectively forever
    svc = AnimatorService(bus=bus, nats_url="nats://unused:4222", stepper_hz=50.0)
    svc._has_u2d2 = True
    svc._reopen_interval_s = 100.0  # don't let reopen interfere with this assertion

    await _run_stepper_for(svc, 0.5)

    assert svc._has_u2d2 is False, (
        "sustained write failures must degrade the bus once the consecutive threshold is exceeded"
    )


async def test_degraded_bus_auto_recovers_via_reopen():
    """After degrading, the stepper must re-open the bus and resume — no restart."""
    bus = RecoverableDxlBus()
    svc = AnimatorService(bus=bus, nats_url="nats://unused:4222", stepper_hz=50.0)
    svc._has_u2d2 = True
    svc._max_write_failures = 3
    svc._reopen_interval_s = 0.05  # poll fast so the test stays quick

    svc._start_stepper(asyncio.get_running_loop())
    try:
        # Device genuinely gone: writes fail AND re-open can't find it.
        bus.device_present = False
        bus.disconnect()
        await asyncio.sleep(0.3)
        assert svc._has_u2d2 is False, "a dropped bus should degrade"
        assert bus.open_calls >= 1, "stepper must keep attempting to re-open a degraded bus"

        # Device returns — the next re-open attempt should succeed and resume.
        bus.device_present = True
        await asyncio.sleep(0.3)
        assert svc._has_u2d2 is True, (
            "bus must auto-recover once re-open succeeds — without a manual restart"
        )
    finally:
        svc._stepper_stop.set()
        if svc._stepper_thread is not None:
            svc._stepper_thread.join(timeout=2)

    # And it actually resumes driving servos after recovery.
    writes_after = len(bus.writes)
    assert writes_after > 0, "stepper must resume writing after recovery"
