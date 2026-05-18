"""Compose Lafufu fortune-card text onto an uploaded letterhead.

Lays body text in the empty middle band of the card (between top + bottom
decoration) and optional lucky-stop / lucky-numbers in a smaller line near
the bottom. Auto-shrinks font size to fit the available region.

Body text and lucky info are drawn separately so each can use its own font
size and the body never bleeds into the lucky line.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# Layout as fractions of letterhead size. Tuned for the lafufu card stock
# (rabbit decoration takes the top ~30%, bottom decoration ~10%).
_BODY_REGION_PCT = (0.08, 0.32, 0.92, 0.78)  # (left, top, right, bottom)
_LUCKY_REGION_PCT = (0.08, 0.79, 0.92, 0.92)

# Bundled fortune-card font — IM Fell English, a 17th-century revival serif
# that matches the woodcut illustration on the lafufu letterhead. Shipped
# inside the package so we don't have to install a font on the Pi.
_BUNDLED_FONT = str(Path(__file__).parent / "fonts" / "IMFellEnglish-Regular.ttf")

# Fallbacks if the bundled file is missing for any reason (e.g. running tests
# from an editable install where assets aren't copied).
_FALLBACKS = [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
]


def _default_font() -> str:
    if Path(_BUNDLED_FONT).exists():
        return _BUNDLED_FONT
    for f in _FALLBACKS:
        if Path(f).exists():
            return f
    return _BUNDLED_FONT  # last resort — PIL will error helpfully if missing


DEFAULT_BODY_FONT = _default_font()
DEFAULT_LUCKY_FONT = _default_font()


def compose_fortune(
    letterhead_path: Path,
    body_text: str,
    *,
    lucky_subway_stop: str | None = None,
    lucky_numbers: list[int] | None = None,
    font_path: str = DEFAULT_BODY_FONT,
    lucky_font_path: str = DEFAULT_LUCKY_FONT,
    output_path: Path | None = None,
) -> Path:
    """Returns the path of a PNG with text composed over the letterhead."""
    img = Image.open(letterhead_path).convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    W, H = img.size

    body_box = _pct_box(W, H, _BODY_REGION_PCT)
    body_font = _fit_font(draw, body_text, font_path, body_box, max_size=int(H * 0.05))
    _draw_centered_block(draw, body_text, body_font, body_box)

    if lucky_subway_stop or lucky_numbers:
        lines = []
        if lucky_subway_stop:
            lines.append(f"Lucky Subway Stop:  {lucky_subway_stop}")
        if lucky_numbers:
            lines.append(f"Lucky numbers: {', '.join(str(n) for n in lucky_numbers)}")
        lucky_text = "\n".join(lines)
        lucky_box = _pct_box(W, H, _LUCKY_REGION_PCT)
        lucky_font = _fit_font(
            draw, lucky_text, lucky_font_path, lucky_box, max_size=int(H * 0.028)
        )
        _draw_centered_block(draw, lucky_text, lucky_font, lucky_box)

    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / "_lafufu_composed.png"
    img.save(output_path, "PNG")
    log.info(
        "composer.fortune body=%dch lucky=%s -> %s",
        len(body_text),
        "yes" if (lucky_subway_stop or lucky_numbers) else "no",
        output_path,
    )
    return output_path


def _pct_box(W: int, H: int, pct: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return (int(pct[0] * W), int(pct[1] * H), int(pct[2] * W), int(pct[3] * H))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """Word-wrap text into multiple lines that fit within max_w pixels. Blank
    lines (paragraph breaks via '\\n\\n') are preserved."""
    out_paragraphs: list[str] = []
    for para in text.split("\n\n"):
        words = para.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        out_paragraphs.append("\n".join(lines))
    return "\n\n".join(out_paragraphs)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    box: tuple[int, int, int, int],
    *,
    max_size: int = 80,
    min_size: int = 10,
) -> ImageFont.FreeTypeFont:
    """Largest font size where wrapped text fits inside box. Halves search step
    near min_size for speed."""
    box_w = box[2] - box[0]
    box_h = box[3] - box[1]
    step = 2
    for size in range(max_size, min_size - 1, -step):
        font = ImageFont.truetype(font_path, size)
        wrapped = _wrap(draw, text, font, box_w)
        spacing = int(size * 0.32)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=spacing)
        if (bbox[2] - bbox[0]) <= box_w and (bbox[3] - bbox[1]) <= box_h:
            return font
    return ImageFont.truetype(font_path, min_size)


def _draw_centered_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
) -> None:
    """Draw multiline text, each line centered horizontally + the whole
    block vertically centered inside the box."""
    box_w = box[2] - box[0]
    box_h = box[3] - box[1]
    wrapped = _wrap(draw, text, font, box_w)
    spacing = int(font.size * 0.32)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=spacing)
    text_h = bbox[3] - bbox[1]
    cx = box[0] + box_w // 2
    y = box[1] + (box_h - text_h) // 2 - bbox[1]
    draw.multiline_text(
        (cx, y), wrapped, font=font, fill="black", align="center", anchor="ma", spacing=spacing
    )
