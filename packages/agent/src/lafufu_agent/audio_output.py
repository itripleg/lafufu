"""Resolve the ALSA playback device for the agent's aplay output.

Goal: audio "just works" whichever USB audio device is connected (the bench
headset today, the Lafufu's built-in speaker in production) WITHOUT a hardcoded
card name — the old ``plughw:CARD=USB`` broke the moment the card enumerated as
"Device". So ``auto`` picks the first non-HDMI card. HDMI is never auto-selected
(Pi HDMI adds lag; the typical monitor has no speakers), but the operator can
select an HDMI card explicitly.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

_CARD_RE = re.compile(r"^card \d+: (\S+) \[")


def _is_hdmi(card: str) -> bool:
    return "hdmi" in card.lower()


def _parse_aplay_l(text: str) -> list[str]:
    """Card short-names from ``aplay -l`` output, in listed order.

    A line looks like: ``card 2: Device [USB Audio Device], device 0: ...`` —
    we want the ``Device`` token. Order is preserved so 'auto' is deterministic.
    """
    cards: list[str] = []
    for line in text.splitlines():
        m = _CARD_RE.match(line)
        if m and m.group(1) not in cards:
            cards.append(m.group(1))
    return cards


def list_output_cards() -> list[str]:
    """ALSA playback card short-names via ``aplay -l``. Empty if aplay missing
    or errors (e.g. off-Linux dev box) — callers fall back to ALSA 'default'."""
    if shutil.which("aplay") is None:
        return []
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, timeout=5, text=True)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("aplay.list_failed error=%s", e)
        return []
    return _parse_aplay_l(out.stdout)


def resolve_output_device(value: str | None, *, cards: list[str] | None = None) -> str:
    """Resolve an ``aplay -D`` device string from the operator's setting.

    ``value`` is one of:
      - ``"auto"`` / ``""`` / ``None`` — pick the first non-HDMI card.
      - a full device string (``plughw:CARD=…``, ``hw:2,0``, ``default``,
        ``sysdefault:CARD=…``) — returned unchanged.
      - a bare ALSA card name (e.g. ``"USB"``, ``"vc4hdmi0"``) — wrapped as
        ``plughw:CARD=<name>,DEV=0``. Explicit names are honored even for HDMI.

    ``cards`` is injectable for tests; production omits it (queries ``aplay -l``).
    """
    v = (value or "").strip()
    if v and v.lower() != "auto":
        # A full device spec passes through; a bare card name is wrapped.
        if (
            ":" in v
            or "/" in v
            or v in ("default", "sysdefault")
            or v.lower().startswith(("plughw", "hw", "dmix", "plug", "sysdefault", "default"))
        ):
            return v
        return f"plughw:CARD={v},DEV=0"

    # auto: first non-HDMI card.
    if cards is None:
        cards = list_output_cards()
    for c in cards:
        if not _is_hdmi(c):
            return f"plughw:CARD={c},DEV=0"
    log.warning("audio.output.auto.no_non_hdmi_card cards=%s — falling back to 'default'", cards)
    return "default"
