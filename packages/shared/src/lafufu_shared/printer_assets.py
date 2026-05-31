"""Resolve the printer's *active* letterhead + font.

The control router (which manages the galleries and writes the active
pointers) and the printer service (which composes + prints) must agree on
which letterhead and font the operator's selection points at. Both read the
same stable data-dir locations here so a choice made in the admin UI is what
the agent's auto-fortune composes onto.

The active letterhead is the single stable path ``<data>/letterhead.png`` that
the control router copies the chosen image into on activate; the active font
is tracked by a ``<data>/active_font`` pointer holding a ``kind/name`` string.
"""

from __future__ import annotations

from pathlib import Path

from .paths import printer_data_dir, printer_default_letterheads_dir

# Stable filenames the control router writes; mirrored here so the printer
# service can resolve the active selection without importing control.
_ACTIVE_LETTERHEAD = "letterhead.png"
_ACTIVE_FONT_POINTER = "active_font"
# Bundled neutral fallback — composing onto white is effectively plain text,
# so compose/print never dead-ends on a fresh install.
_WHITE_LETTERHEAD = "white.png"


def active_letterhead_path() -> Path:
    """Path to the letterhead compose/print should draw on.

    Returns the operator's active letterhead if one has been activated, else
    the bundled white card so a fresh install still composes onto *something*.

    The white fallback lives in the repo assets dir, OUTSIDE the printer data
    dir. A caller that resolves the letterhead itself (the printer service for
    its compose_fortune intent) is trusted and feeds it straight to the
    composer — it must NOT run this through the data-dir path-safety check that
    guards untrusted NATS-supplied paths, or the fallback would be rejected.
    """
    active = printer_data_dir() / _ACTIVE_LETTERHEAD
    if active.is_file():
        return active
    return printer_default_letterheads_dir() / _WHITE_LETTERHEAD


def active_font_name() -> str | None:
    """Bare filename of the active compose font, or ``None`` for the composer's
    bundled default.

    Reads the ``kind/name`` pointer the control router writes on font
    activation and returns just the ``name`` part — which the printer service
    resolves against its font dirs. Missing or empty pointer → ``None``.
    """
    pointer = printer_data_dir() / _ACTIVE_FONT_POINTER
    try:
        raw = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return raw.split("/", 1)[-1]
