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
ADDR_TORQUE_ENABLE = 64
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
        from dynamixel_sdk import PacketHandler, PortHandler  # lazy

        self._PortHandler = PortHandler
        self._PacketHandler = PacketHandler
        self._port_name = port
        self._baud = baud
        self._port = None
        self._packet = PacketHandler(2.0)

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
                    if comm_result == 0:  # COMM_SUCCESS
                        self._port = handler
                        self._port_name = p
                        self._baud = b
                        log.info("dxl.bus.open port=%s baud=%d", p, b)
                        return
                handler.closePort()
            except Exception as e:
                log.debug("dxl.probe.failed port=%s error=%s", p, e)

        raise ConnectionError(f"U2D2 not found on any of {list(candidates_ports)}")

    def enable_torque(self) -> None:
        for _, dxl_id in pose.DXL_IDS.items():
            self._packet.write1ByteTxRx(self._port, dxl_id, ADDR_TORQUE_ENABLE, 1)

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
        self._packet.write4ByteTxRx(self._port, dxl_id, ADDR_GOAL_POSITION, int(position))

    def read(self, name: str) -> int:
        if self._port is None:
            raise OSError("DXL bus not open")
        dxl_id = pose.DXL_IDS[name]
        val, _, _ = self._packet.read4ByteTxRx(self._port, dxl_id, ADDR_PRESENT_POSITION)
        return int(val)

    def close(self) -> None:
        self.disable_torque()
        if self._port:
            self._port.closePort()
            self._port = None
