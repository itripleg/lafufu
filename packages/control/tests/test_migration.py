"""Tests for the one-shot letterhead-data migration."""


def _override_image_dir(monkeypatch, tmp_path):
    """image_letterheads_dir has no env override; monkeypatch it for isolation."""
    from lafufu_shared import paths as shared_paths

    target = tmp_path / "images" / "letterheads"
    monkeypatch.setattr(shared_paths, "image_letterheads_dir", lambda: target)
    return target


def test_moves_uploads_and_pointer(tmp_path, monkeypatch):
    """Seed the old layout, run migrate_letterhead_data, assert new layout."""
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path / "printer"))
    new_dir = _override_image_dir(monkeypatch, tmp_path)

    old_data = tmp_path / "printer"
    (old_data / "uploads").mkdir(parents=True)
    (old_data / "uploads" / "u1.png").write_bytes(b"u1")
    (old_data / "uploads" / "u2.png").write_bytes(b"u2")
    (old_data / "letterhead.png").write_bytes(b"active")
    (old_data / "active_letterhead").write_text("upload/u1.png", encoding="utf-8")

    # Re-import migrate inside the test so any patched helpers are in effect.
    from lafufu_control.migration import migrate_letterhead_data

    migrate_letterhead_data()

    # New layout populated.
    assert (new_dir / "u1.png").read_bytes() == b"u1"
    assert (new_dir / "u2.png").read_bytes() == b"u2"
    new_active_png = new_dir.parent / "active_letterhead.png"
    assert new_active_png.read_bytes() == b"active"
    new_pointer = new_dir.parent / "active_letterhead"
    assert new_pointer.read_text(encoding="utf-8") == "letterheads/upload/u1.png"

    # Old layout cleaned up.
    assert not (old_data / "uploads" / "u1.png").exists()
    assert not (old_data / "letterhead.png").exists()
    assert not (old_data / "active_letterhead").exists()


def test_idempotent(tmp_path, monkeypatch):
    """If new layout exists and has files, the migration is a no-op."""
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path / "printer"))
    new_dir = _override_image_dir(monkeypatch, tmp_path)

    # Pre-populate new layout (simulates a second run).
    new_dir.mkdir(parents=True)
    (new_dir / "fresh.png").write_bytes(b"fresh")

    # Also seed old layout with a file that would otherwise be moved.
    old_uploads = tmp_path / "printer" / "uploads"
    old_uploads.mkdir(parents=True)
    (old_uploads / "should_not_move.png").write_bytes(b"old")

    from lafufu_control.migration import migrate_letterhead_data

    migrate_letterhead_data()

    # New file intact.
    assert (new_dir / "fresh.png").read_bytes() == b"fresh"
    # Old file untouched (migration no-op'd).
    assert (old_uploads / "should_not_move.png").exists()
    assert not (new_dir / "should_not_move.png").exists()
