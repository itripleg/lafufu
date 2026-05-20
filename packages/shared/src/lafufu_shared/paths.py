"""Filesystem paths shared between the control and printer services.

Both processes must agree on where printer assets live — the printer
service's path-safety check rejects anything outside the data dir, so a
silent mismatch would break printing. Centralising the computation here is
the single source of truth that keeps the two sides from drifting.
"""

import os
from pathlib import Path

# lafufu_shared lives at <repo>/packages/shared/src/lafufu_shared/paths.py —
# four parents up from this file is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]


def repo_root() -> Path:
    return _REPO_ROOT


def printer_data_dir() -> Path:
    """Runtime printer data — uploaded letterheads/fonts, the active
    letterhead, composed output. Override with ``LAFUFU_PRINTER_DATA_DIR``;
    defaults to ``<repo>/data/printer`` so local dev and the Pi behave
    identically (the old hardcoded ``/srv/lafufu/...`` broke on dev)."""
    env = os.environ.get("LAFUFU_PRINTER_DATA_DIR")
    return Path(env) if env else _REPO_ROOT / "data" / "printer"


def printer_uploads_dir() -> Path:
    """Operator-uploaded letterhead images (gitignored runtime data)."""
    return printer_data_dir() / "uploads"


def printer_fonts_upload_dir() -> Path:
    """Operator-uploaded fonts (gitignored runtime data)."""
    return printer_data_dir() / "fonts"


def printer_default_letterheads_dir() -> Path:
    """Letterhead images shipped with the repo — always present in the gallery."""
    return _REPO_ROOT / "assets" / "printer" / "letterheads"


def printer_default_fonts_dir() -> Path:
    """Fonts shipped with the repo — always present in the font picker."""
    return _REPO_ROOT / "assets" / "printer" / "fonts"
