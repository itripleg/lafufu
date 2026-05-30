"""Real Dynamixel U2D2 bus wrapper.

For tests, use `lafufu_shared.testing.FakeDxlBus` instead.
On the Pi: auto-detects /dev/ttyUSB* and tries common baud rates.
If no bus is available, raises ConnectionError — caller handles by going degraded.
"""

import contextlib
import glob
import logging
import platform
from collections.abc import Iterable

from . import pose

log = logging.getLogger(__name__)

# Control table addresses (Dynamixel X-series, protocol 2.0)
ADDR_MAX_POSITION_LIMIT = 48  # EEPROM — write with torque OFF
ADDR_MIN_POSITION_LIMIT = 52  # EEPROM — write with torque OFF
ADDR_TORQUE_ENABLE = 64
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132


def default_port_candidates() -> list[str]:
    if platform.system().lower() == "linux":
        return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    # Windows fallback
    return [f"COM{i}" for i in range(1, 30)]


def default_bauds() -> list[int]:
    return [57600, 115200, 1_000_000, 2_000_000, 3_000_000, 4_000_000]


class DxlBus:
    """Real DXL bus. Lazy-import dynamixel_sdk so unit tests don't need it."""

    def __init__(self, port: str | None = None, baud: int | None = None) -> None:
        from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler  # lazy

        self._PortHandler = PortHandler
        self._PacketHandler = PacketHandler
        self._COMM_SUCCESS = COMM_SUCCESS
        self._port_name = port
        self._baud = baud
        self._port = None
        self._packet = PacketHandler(2.0)

    def _check(self, comm_result: int, dxl_error: int, what: str) -> None:
        """Raise OSError if a DXL transaction failed.

        ``comm_result`` is the transport status; ``dxl_error`` is the servo's
        hardware error byte (overload / overheat / etc). Either being non-OK
        means the command did not land — surfacing it as OSError lets the
        service drop to a degraded state instead of silently commanding a
        dead or stalled servo.
        """
        if comm_result != self._COMM_SUCCESS:
            raise OSError(f"DXL {what}: comm failed (result={comm_result})")
        if dxl_error != 0:
            raise OSError(f"DXL {what}: servo fault (error_byte={dxl_error})")

    def open(self) -> None:
        """Open the bus. Tries provided port/baud, else auto-detects."""
        candidates_ports: Iterable[str] = (
            [self._port_name] if self._port_name else default_port_candidates()
        )
        candidates_bauds: Iterable[int] = [self._baud] if self._baud else default_bauds()

        for p in candidates_ports:
            try:
                handler = self._PortHandler(p)
                if not handler.openPort():
                    continue
                for b in candidates_bauds:
                    if not handler.setBaudRate(b):
                        continue
                    # Probe motor 1 to verify the bus is alive
                    _, comm_result, _ = self._packet.read4ByteTxRx(
                        handler, 1, ADDR_PRESENT_POSITION
                    )
                    if comm_result == self._COMM_SUCCESS:
                        self._port = handler
                        self._port_name = p
                        self._baud = b
                        log.info("dxl.bus.open port=%s baud=%d", p, b)
                        return
                handler.closePort()
            except Exception as e:
                log.debug("dxl.probe.failed port=%s error=%s", p, e)

        raise ConnectionError(f"U2D2 not found on any of {list(candidates_ports)}")

    def configure_limits(self) -> None:
        """Write per-servo hardware safety limits.

        Min/Max Position Limits make the servo itself reject an out-of-range
        goal; Profile Velocity/Acceleration cap how fast and hard it slews — a
        backstop beneath the software PoseSmoother. Must be called with torque
        OFF (after open(), before enable_torque()) — the position-limit
        registers are EEPROM and reject writes while torque is enabled.
        """
        if self._port is None:
            raise OSError("DXL bus not open")
        for name, dxl_id in pose.DXL_IDS.items():
            lo, hi = min(pose.CLAMP[name]), max(pose.CLAMP[name])
            for addr, value in (
                (ADDR_MIN_POSITION_LIMIT, lo),
                (ADDR_MAX_POSITION_LIMIT, hi),
                # Per-servo: the jaw runs unlimited so it can snap for lipsync;
                # head/eye/brow keep the calm safety-cap profile.
                (ADDR_PROFILE_VELOCITY, pose.profile_velocity(name)),
                (ADDR_PROFILE_ACCELERATION, pose.profile_acceleration(name)),
            ):
                comm, err = self._packet.write4ByteTxRx(self._port, dxl_id, addr, int(value))
                self._check(comm, err, f"configure_limits {name}")

    def enable_torque(self) -> None:
        if self._port is None:
            raise OSError("DXL bus not open")
        for name, dxl_id in pose.DXL_IDS.items():
            comm, err = self._packet.write1ByteTxRx(self._port, dxl_id, ADDR_TORQUE_ENABLE, 1)
            self._check(comm, err, f"enable_torque {name}")

    def disable_torque(self) -> None:
        if self._port is None:
            return
        for _, dxl_id in pose.DXL_IDS.items():
            with contextlib.suppress(Exception):
                self._packet.write1ByteTxRx(self._port, dxl_id, ADDR_TORQUE_ENABLE, 0)

    def write(self, name: str, position: int) -> None:
        if self._port is None:
            raise OSError("DXL bus not open")
        dxl_id = pose.DXL_IDS[name]
        comm, err = self._packet.write4ByteTxRx(
            self._port, dxl_id, ADDR_GOAL_POSITION, int(position)
        )
        self._check(comm, err, f"write {name}")

    def read(self, name: str) -> int:
        if self._port is None:
            raise OSError("DXL bus not open")
        dxl_id = pose.DXL_IDS[name]
        val, comm, err = self._packet.read4ByteTxRx(self._port, dxl_id, ADDR_PRESENT_POSITION)
        self._check(comm, err, f"read {name}")
        return int(val)

    def close(self) -> None:
        self.disable_torque()
        if self._port:
            self._port.closePort()
            self._port = None
