import pytest
from lafufu_animator import pose
from lafufu_animator.dxl_bus import (
    ADDR_MAX_POSITION_LIMIT,
    ADDR_MIN_POSITION_LIMIT,
    ADDR_PROFILE_ACCELERATION,
    ADDR_PROFILE_VELOCITY,
    DxlBus,
)
from lafufu_shared.testing import FakeDxlBus

# DXL comm-result codes: 0 = COMM_SUCCESS, anything else = transport failure.
_COMM_OK = 0
_COMM_FAIL = -1001  # COMM_TX_FAIL


class _FakePacket:
    """Stand-in for dynamixel_sdk PacketHandler — returns canned comm/error
    codes so the bus's result-checking can be tested without hardware."""

    def __init__(self, comm: int = _COMM_OK, err: int = 0, value: int = 2048) -> None:
        self.comm = comm
        self.err = err
        self.value = value
        self.writes: list[tuple[int, int, int]] = []

    def _w(self, port, dxl_id, addr, val):
        self.writes.append((dxl_id, addr, val))
        return (self.comm, self.err)

    write1ByteTxRx = _w
    write2ByteTxRx = _w
    write4ByteTxRx = _w

    def read4ByteTxRx(self, port, dxl_id, addr):
        return (self.value, self.comm, self.err)


def _bus(comm: int = _COMM_OK, err: int = 0, value: int = 2048, *, open_port: bool = True):
    """A DxlBus with a fake packet handler and (optionally) a fake open port."""
    bus = DxlBus(port="FAKE", baud=57600)
    bus._packet = _FakePacket(comm=comm, err=err, value=value)
    bus._port = object() if open_port else None
    return bus


def test_write_succeeds_on_comm_success():
    _bus(comm=_COMM_OK).write("jaw", 1700)  # no exception


def test_write_raises_on_comm_failure():
    with pytest.raises(OSError):
        _bus(comm=_COMM_FAIL).write("jaw", 1700)


def test_write_raises_on_hardware_error_byte():
    # err != 0 means the servo is in a fault latch (overload/overheat) — a
    # stalled servo must not look healthy.
    with pytest.raises(OSError):
        _bus(comm=_COMM_OK, err=1).write("jaw", 1700)


def test_write_raises_when_port_not_open():
    with pytest.raises(OSError):
        _bus(open_port=False).write("jaw", 1700)


def test_read_returns_value_on_comm_success():
    assert _bus(comm=_COMM_OK, value=2099).read("head_lr") == 2099


def test_read_raises_on_comm_failure():
    with pytest.raises(OSError):
        _bus(comm=_COMM_FAIL).read("head_lr")


def test_enable_torque_raises_when_port_not_open():
    with pytest.raises(OSError):
        _bus(open_port=False).enable_torque()


def test_enable_torque_raises_on_comm_failure():
    with pytest.raises(OSError):
        _bus(comm=_COMM_FAIL).enable_torque()


def test_configure_limits_writes_position_limits_per_servo():
    """Min/Max Position Limits written to hardware from pose.CLAMP — the servo
    itself then refuses an out-of-range goal, independent of software clamping."""
    bus = _bus()
    bus.configure_limits()
    writes = bus._packet.writes
    for name, dxl_id in pose.DXL_IDS.items():
        lo, hi = min(pose.CLAMP[name]), max(pose.CLAMP[name])
        assert (dxl_id, ADDR_MIN_POSITION_LIMIT, lo) in writes
        assert (dxl_id, ADDR_MAX_POSITION_LIMIT, hi) in writes


def test_configure_limits_writes_velocity_and_acceleration_backstop():
    """Profile Velocity + Acceleration capped in hardware as a safety backstop
    for the CALM servos (head/eye/brow). The jaw is the exception — see
    test_configure_limits_jaw_snaps_faster_than_head."""
    bus = _bus()
    bus.configure_limits()
    writes = bus._packet.writes
    for name, dxl_id in pose.DXL_IDS.items():
        if name == "jaw":
            continue
        assert (dxl_id, ADDR_PROFILE_VELOCITY, pose.PROFILE_VELOCITY) in writes
        assert (dxl_id, ADDR_PROFILE_ACCELERATION, pose.PROFILE_ACCELERATION) in writes


def test_configure_limits_jaw_snaps_faster_than_head():
    """The jaw must SNAP for lipsync. The calm head profile (300/80) throttled
    it so brief loud syllables never physically reached full open — the software
    commands ~full range, the servo's onboard profile was the cap. The jaw gets
    an unlimited profile (0 = max in the X-series velocity-based profile),
    matching the debug testbed's gold-reference JawBus, which sets no profile."""
    bus = _bus()
    bus.configure_limits()
    writes = bus._packet.writes
    jaw_id = pose.DXL_IDS["jaw"]
    head_id = pose.DXL_IDS["head_lr"]
    assert (jaw_id, ADDR_PROFILE_VELOCITY, 0) in writes
    assert (jaw_id, ADDR_PROFILE_ACCELERATION, 0) in writes
    # The jaw profile must differ from the calm head profile.
    assert (jaw_id, ADDR_PROFILE_VELOCITY, pose.PROFILE_VELOCITY) not in writes
    assert (head_id, ADDR_PROFILE_VELOCITY, pose.PROFILE_VELOCITY) in writes


def test_configure_limits_raises_when_port_not_open():
    with pytest.raises(OSError):
        _bus(open_port=False).configure_limits()


def test_configure_limits_raises_on_comm_failure():
    with pytest.raises(OSError):
        _bus(comm=_COMM_FAIL).configure_limits()


def test_fake_bus_records_writes():
    bus = FakeDxlBus()
    bus.write("jaw", 1700)
    bus.write("jaw", 1750)
    assert bus.last_position("jaw") == 1750
    assert bus.writes == [("jaw", 1700), ("jaw", 1750)]


def test_disconnect_raises_on_write():
    bus = FakeDxlBus()
    bus.disconnect()
    with pytest.raises(IOError):
        bus.write("jaw", 1700)


def test_reconnect_clears_error_state():
    bus = FakeDxlBus()
    bus.disconnect()
    bus.reconnect()
    bus.write("jaw", 1700)
    assert bus.last_position("jaw") == 1700


def test_disable_torque_counted():
    bus = FakeDxlBus()
    bus.disable_torque()
    bus.disable_torque()
    assert bus.torque_disabled_count == 2
