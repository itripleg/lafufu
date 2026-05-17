import pytest
from lafufu_shared.testing import FakeDxlBus, nats_server_fixture

# Import the fixture so pytest discovers it
nats_server = nats_server_fixture("4235")


async def test_nats_fixture_yields_url(nats_server):
    assert nats_server.startswith("nats://localhost:4235")


def test_fake_dxl_bus_records_writes():
    bus = FakeDxlBus()
    bus.write("jaw", 1700)
    bus.write("jaw", 1750)
    bus.write("head_lr", 2100)
    assert bus.writes == [("jaw", 1700), ("jaw", 1750), ("head_lr", 2100)]
    assert bus.last_position("jaw") == 1750
    assert bus.last_position("head_lr") == 2100


def test_fake_dxl_bus_disconnect_raises_on_write():
    bus = FakeDxlBus()
    bus.disconnect()
    with pytest.raises(IOError, match="disconnected"):
        bus.write("jaw", 1700)
