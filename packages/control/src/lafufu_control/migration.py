"""One-shot migration: relocate printer letterhead data into data/images/letterheads/.

Runs at create_app() startup. Idempotent: no-ops if the new layout is already
populated.
"""

import logging
import shutil

import lafufu_shared.paths as _paths

log = logging.getLogger("lafufu_control.migration")


def migrate_letterhead_data() -> None:
    new_dir = _paths.image_letterheads_dir()
    # Idempotency check: any file in the new dir means migration already ran.
    if new_dir.exists() and any(new_dir.iterdir()):
        return

    old_data = _paths.printer_data_dir()
    old_uploads = old_data / "uploads"
    old_active_png = old_data / "letterhead.png"
    old_pointer = old_data / "active_letterhead"

    new_active_png = new_dir.parent / "active_letterhead.png"
    new_pointer = new_dir.parent / "active_letterhead"

    new_dir.mkdir(parents=True, exist_ok=True)

    # 1. Move uploaded letterheads.
    if old_uploads.is_dir():
        for src in old_uploads.iterdir():
            if not src.is_file():
                continue
            dst = new_dir / src.name
            if dst.exists():
                continue
            try:
                shutil.move(str(src), str(dst))
                log.info("migrated upload %s -> %s", src.name, dst)
            except OSError as e:
                log.warning("failed to migrate upload %s: %s", src.name, e)

    # 2. Move the active letterhead PNG.
    if old_active_png.is_file() and not new_active_png.exists():
        try:
            shutil.move(str(old_active_png), str(new_active_png))
            log.info("migrated active letterhead png -> %s", new_active_png)
        except OSError as e:
            log.warning("failed to migrate active letterhead png: %s", e)

    # 3. Rewrite the pointer.
    if old_pointer.is_file() and not new_pointer.exists():
        try:
            old_value = old_pointer.read_text(encoding="utf-8").strip()
            if old_value:
                new_value = f"letterheads/{old_value}"
                new_pointer.write_text(new_value, encoding="utf-8")
                old_pointer.unlink()
                log.info("rewrote active_letterhead pointer: %s -> %s", old_value, new_value)
        except OSError as e:
            log.warning("failed to rewrite active letterhead pointer: %s", e)
