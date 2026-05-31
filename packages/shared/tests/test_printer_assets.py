"""Tests for the shared active-letterhead/active-font resolver.

The printer service and control router must agree on which letterhead + font
the *active* selection points at. These pin that contract: the resolver reads
the same data-dir pointers the control router writes on activation, and falls
back to the bundled white card so a fresh install still composes onto
something.
"""

from lafufu_shared import printer_assets


def test_active_letterhead_returns_data_dir_file_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path))
    active = tmp_path / "letterhead.png"
    active.write_bytes(b"\x89PNG\r\n")  # not a real PNG; existence is what matters

    assert printer_assets.active_letterhead_path() == active


def test_active_letterhead_falls_back_to_bundled_white(tmp_path, monkeypatch):
    # Empty data dir → no activated letterhead → bundled white card.
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path))

    p = printer_assets.active_letterhead_path()

    assert p.name == "white.png"
    assert "assets" in p.parts  # the repo-bundled default, not the data dir


def test_active_font_name_none_when_pointer_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path))
    assert printer_assets.active_font_name() is None


def test_active_font_name_strips_kind_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path))
    (tmp_path / "active_font").write_text("upload/MyFont.ttf", encoding="utf-8")

    assert printer_assets.active_font_name() == "MyFont.ttf"


def test_active_font_name_none_when_pointer_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path))
    (tmp_path / "active_font").write_text("   ", encoding="utf-8")

    assert printer_assets.active_font_name() is None
