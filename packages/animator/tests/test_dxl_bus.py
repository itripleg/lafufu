import pytest
from lafufu_shared.testing import FakeDxlBus


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
