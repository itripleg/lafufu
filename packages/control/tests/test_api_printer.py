"""Tests for the printer letterhead + font gallery router."""

import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.api.routers.printer import _safe_name
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_shared.paths import printer_default_fonts_dir, printer_default_letterheads_dir
from PIL import Image


def _png_bytes(color: str = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 36), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point printer runtime data at a throwaway dir so tests never touch the
    # real data/printer folder.
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path / "printer"))
    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    c = TestClient(app)
    c.published = published  # type: ignore[attr-defined]
    return c


def test_safe_name_rejects_traversal():
    for bad in ("../etc", "a/b", "..", ".", "", "x\\y"):
        with pytest.raises(HTTPException):
            _safe_name(bad)
    assert _safe_name("card.png") == "card.png"


def test_list_letterheads_includes_bundled_defaults(client):
    r = client.get("/api/printer/letterheads")
    assert r.status_code == 200
    items = r.json()["items"]
    names = {i["name"] for i in items if i["kind"] == "default"}
    # The repo ships these default cards.
    assert {"card.png", "cardEmpty.png", "card_white.png"} <= names
    # Nothing is active until the operator picks one.
    assert all(not i["active"] for i in items)


def test_upload_letterhead_becomes_active(client):
    r = client.post(
        "/api/printer/letterhead",
        files={"file": ("my card.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "upload"
    # Client filename is sanitised (space → dash).
    assert "/" not in body["name"] and " " not in body["name"]

    listing = client.get("/api/printer/letterheads").json()["items"]
    upload = next(i for i in listing if i["kind"] == "upload")
    assert upload["active"] is True
    # The active letterhead is served.
    assert client.get("/api/printer/letterhead").status_code == 200


def test_activate_default_letterhead(client):
    r = client.post("/api/printer/letterheads/default/cardEmpty.png/activate")
    assert r.status_code == 200
    listing = client.get("/api/printer/letterheads").json()["items"]
    active = [i for i in listing if i["active"]]
    assert len(active) == 1
    assert active[0]["kind"] == "default" and active[0]["name"] == "cardEmpty.png"


def test_get_letterhead_file_serves_default(client):
    r = client.get("/api/printer/letterheads/default/card.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_cannot_delete_a_bundled_default(client):
    r = client.delete("/api/printer/letterheads/default/card.png")
    assert r.status_code == 400
    # The default file is still on disk.
    assert (printer_default_letterheads_dir() / "card.png").is_file()


def test_delete_uploaded_letterhead(client):
    name = client.post(
        "/api/printer/letterhead",
        files={"file": ("x.png", _png_bytes(), "image/png")},
    ).json()["name"]
    r = client.delete(f"/api/printer/letterheads/upload/{name}")
    assert r.status_code == 204
    listing = client.get("/api/printer/letterheads").json()["items"]
    assert all(i["name"] != name for i in listing)


def test_upload_rejects_non_image(client):
    r = client.post(
        "/api/printer/letterhead",
        files={"file": ("evil.png", b"not really a png", "image/png")},
    )
    assert r.status_code == 400


def test_list_fonts_includes_bundled_default(client):
    items = client.get("/api/printer/fonts").json()["items"]
    names = {i["name"] for i in items if i["kind"] == "default"}
    assert "IMFellEnglish-Regular.ttf" in names


def test_activate_and_upload_font(client):
    # Activate the bundled default.
    r = client.post("/api/printer/fonts/default/IMFellEnglish-Regular.ttf/activate")
    assert r.status_code == 200
    # Upload a font (reuse the real bundled TTF bytes as valid font data).
    ttf = (printer_default_fonts_dir() / "IMFellEnglish-Regular.ttf").read_bytes()
    r = client.post("/api/printer/font", files={"file": ("custom.ttf", ttf, "font/ttf")})
    assert r.status_code == 200
    items = client.get("/api/printer/fonts").json()["items"]
    upload = next(i for i in items if i["kind"] == "upload")
    assert upload["active"] is True  # upload becomes the active font


def test_upload_rejects_non_font(client):
    r = client.post("/api/printer/font", files={"file": ("bad.ttf", b"not a font", "font/ttf")})
    assert r.status_code == 400


def test_compose_carries_active_font(client):
    client.post("/api/printer/letterheads/default/cardEmpty.png/activate")
    client.post("/api/printer/fonts/default/IMFellEnglish-Regular.ttf/activate")
    r = client.post("/api/printer/compose", json={"text": "hello fortune"})
    assert r.status_code == 202
    compose_msgs = [p for s, p in client.published if s == "printer.intent.compose"]  # type: ignore[attr-defined]
    assert len(compose_msgs) == 1
    assert compose_msgs[0]["font"] == "IMFellEnglish-Regular.ttf"


def test_compose_without_letterhead_404s(client):
    r = client.post("/api/printer/compose", json={"text": "x"})
    assert r.status_code == 404
