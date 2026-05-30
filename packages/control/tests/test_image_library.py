"""Tests for the generic image library (/api/images/{bucket}/...)."""

import io

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db
from PIL import Image


def _png_bytes(color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 36), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path / "printer"))
    # The image library has its own runtime root that isn't env-overridable yet —
    # monkeypatch the path helpers directly on the image_library module (which
    # imported them by name) so uploads land in tmp_path during tests.
    from lafufu_control.api import image_library

    monkeypatch.setattr(
        image_library, "image_letterheads_dir", lambda: tmp_path / "images" / "letterheads"
    )
    monkeypatch.setattr(image_library, "image_sprites_dir", lambda: tmp_path / "images" / "sprites")
    monkeypatch.setattr(
        image_library, "image_sprites_defaults_dir", lambda: tmp_path / "assets-images" / "sprites"
    )
    # printer_default_letterheads_dir is NOT monkeypatched — it points at the real
    # repo bundled defaults, which is what we want for the "default kind" test.

    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    c = TestClient(app)
    c.published = published  # type: ignore[attr-defined]
    c.sprites_dir = tmp_path / "images" / "sprites"  # type: ignore[attr-defined]
    return c


def test_list_letterheads_bucket(client):
    """GET /api/images/letterheads returns built-in defaults (card.png, white.png)."""
    r = client.get("/api/images/letterheads")
    assert r.status_code == 200
    items = r.json()["items"]
    default_names = {i["name"] for i in items if i["kind"] == "default"}
    assert {"card.png", "white.png"} <= default_names


def test_list_sprites_bucket(client):
    """GET /api/images/sprites returns only IMAGE_EXTS items, ignoring others."""
    # Drop a couple sprite-shaped files into the runtime sprites dir.
    # Use client.sprites_dir (set by the fixture) so both the write and the
    # listing hit the same monkeypatched path.
    sprites_runtime = client.sprites_dir  # type: ignore[attr-defined]
    sprites_runtime.mkdir(parents=True, exist_ok=True)
    (sprites_runtime / "happy_a.png").write_bytes(_png_bytes("blue"))
    (sprites_runtime / "ignored.txt").write_text("nope")  # non-image, should be filtered
    r = client.get("/api/images/sprites")
    assert r.status_code == 200
    names = [i["name"] for i in r.json()["items"]]
    assert "happy_a.png" in names
    assert "ignored.txt" not in names


def test_upload_serve_delete_sprite(client):
    """Upload a sprite, GET it, DELETE it, confirm gone."""
    data = _png_bytes("green")
    r = client.post(
        "/api/images/sprites/upload",
        files={"file": ("test-sprite.png", data, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["kind"] == "upload"
    name = body["name"]

    # GET serves it.
    r2 = client.get(f"/api/images/sprites/upload/{name}")
    assert r2.status_code == 200
    assert r2.content == data

    # DELETE removes it.
    r3 = client.delete(f"/api/images/sprites/upload/{name}")
    assert r3.status_code == 204

    # GET now 404s.
    r4 = client.get(f"/api/images/sprites/upload/{name}")
    assert r4.status_code == 404


def test_unknown_bucket_404(client):
    """GET /api/images/wat returns 404 (bad_bucket)."""
    r = client.get("/api/images/wat")
    assert r.status_code == 404


def test_serves_real_default_frame_sprite(tmp_path):
    """Regression: the Studio's animation frames live in
    assets/images/sprites/default/ and the seed references them as
    sprites/default/<name>. image_sprites_defaults_dir() must resolve to that
    subdir — not its parent — or every idle_/laugh_ frame 404s and the Studio
    shows broken image links.

    Uses the REAL bundled-asset paths (no monkeypatch) so it catches a resolver
    that points one level too high (which the monkeypatched `client` fixture
    can't)."""
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    c = TestClient(app)
    for name in ("idle_01.png", "laugh_01.png", "lafufu_happy.png"):
        r = c.get(f"/api/images/sprites/default/{name}")
        assert r.status_code == 200, f"{name} -> {r.status_code} (Studio frame not served)"
        assert r.headers["content-type"].startswith("image/")
