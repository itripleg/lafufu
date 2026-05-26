"""P3.8 — server-side servo position clamping tests.

Verifies that /preview and /set_pose clamp caller-supplied positions against
pose.CLAMP before publishing the NATS intent, so hardware safety is enforced
at the API boundary regardless of client-side validation.
"""

import pytest
from fastapi.testclient import TestClient
from lafufu_animator.pose import CLAMP, clamp_dxl
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client_and_published(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    published: list[tuple[str, dict]] = []

    def nats_publish(subject: str, payload: dict) -> None:
        published.append((subject, payload))

    app = create_app(engine=engine, nats_publish=nats_publish)
    return TestClient(app), published


# ---------------------------------------------------------------------------
# /preview clamping
# ---------------------------------------------------------------------------


def test_preview_in_range_passes_through_unchanged(client_and_published):
    client, published = client_and_published
    lo, hi = sorted(CLAMP["jaw"])
    in_range = (lo + hi) // 2
    r = client.post("/api/animator/preview", json={"name": "jaw", "position": in_range})
    assert r.status_code == 202
    assert len(published) == 1
    subject, payload = published[0]
    assert subject == "animator.intent.preview"
    assert payload["position"] == in_range


def test_preview_below_range_is_clamped_to_min(client_and_published):
    client, published = client_and_published
    lo, _hi = sorted(CLAMP["head_lr"])
    too_low = lo - 500
    expected = clamp_dxl("head_lr", too_low)
    r = client.post("/api/animator/preview", json={"name": "head_lr", "position": too_low})
    assert r.status_code == 202
    _, payload = published[0]
    assert payload["position"] == expected
    assert payload["position"] != too_low


def test_preview_above_range_is_clamped_to_max(client_and_published):
    client, published = client_and_published
    _lo, hi = sorted(CLAMP["eye"])
    too_high = hi + 500
    expected = clamp_dxl("eye", too_high)
    r = client.post("/api/animator/preview", json={"name": "eye", "position": too_high})
    assert r.status_code == 202
    _, payload = published[0]
    assert payload["position"] == expected
    assert payload["position"] != too_high


# ---------------------------------------------------------------------------
# /set_pose clamping
# ---------------------------------------------------------------------------


def _in_range_pose() -> dict:
    pose = {}
    for servo, (a, b) in CLAMP.items():
        lo, hi = min(a, b), max(a, b)
        pose[servo] = (lo + hi) // 2
    return pose


def test_set_pose_in_range_passes_through(client_and_published):
    client, published = client_and_published
    pose = _in_range_pose()
    r = client.post("/api/animator/set_pose", json=pose)
    assert r.status_code == 202
    _, payload = published[0]
    for servo in ("head_lr", "head_ud", "eye", "jaw", "brow"):
        assert payload["pose"][servo] == pose[servo]


def test_set_pose_out_of_range_servo_is_clamped(client_and_published):
    client, published = client_and_published
    pose = _in_range_pose()
    # Drive brow far outside range
    _lo, hi = sorted(CLAMP["brow"])
    pose["brow"] = hi + 1000
    expected_brow = clamp_dxl("brow", pose["brow"])
    r = client.post("/api/animator/set_pose", json=pose)
    assert r.status_code == 202
    _, payload = published[0]
    assert payload["pose"]["brow"] == expected_brow
    assert payload["pose"]["brow"] != pose["brow"]
    # Other servos unaffected
    for servo in ("head_lr", "head_ud", "eye", "jaw"):
        assert payload["pose"][servo] == pose[servo]
