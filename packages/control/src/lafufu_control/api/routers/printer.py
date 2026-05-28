"""Printer assets — letterhead + font galleries, upload/activate/delete, and
the print/compose intents.

Letterheads and fonts each come in two kinds:
  - "default": shipped with the repo (assets/printer/...), always available.
  - "upload":  operator-uploaded into the runtime data dir (gitignored).

The *active* letterhead is copied into ``data/printer/letterhead.png`` so the
printer service always prints from one stable in-data-dir path — its path
safety check needs nothing special. The active font is tracked by name and
passed to the compose intent.
"""

import io
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from lafufu_shared.paths import (
    printer_data_dir,
    printer_default_fonts_dir,
    printer_default_letterheads_dir,
    printer_fonts_upload_dir,
    printer_uploads_dir,
)
from pydantic import BaseModel, Field

from ..image_library import (
    atomic_write as _atomic_write,
)
from ..image_library import (
    safe_name as _safe_name,
)
from ..image_library import (
    sanitize_upload_name as _sanitize_upload_name,
)

router = APIRouter()

_ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_FONT_EXTS = {".ttf", ".otf"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_FONT_BYTES = 5 * 1024 * 1024  # 5 MB

_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


# ───────────────────────── paths ──────────────────────────


def _data_dir() -> Path:
    return printer_data_dir()


def _letterhead_path() -> Path:
    """The active letterhead — the file print/compose actually consume."""
    return _data_dir() / "letterhead.png"


def _active_letterhead_file() -> Path:
    return _data_dir() / "active_letterhead"


def _active_font_file() -> Path:
    return _data_dir() / "active_font"


def _letterhead_dir(kind: str) -> Path:
    if kind == "default":
        return printer_default_letterheads_dir()
    if kind == "upload":
        return printer_uploads_dir()
    raise HTTPException(404, detail={"error_code": "bad_kind", "message": f"unknown kind {kind!r}"})


def _font_dir(kind: str) -> Path:
    if kind == "default":
        return printer_default_fonts_dir()
    if kind == "upload":
        return printer_fonts_upload_dir()
    raise HTTPException(404, detail={"error_code": "bad_kind", "message": f"unknown kind {kind!r}"})


# ───────────────────────── helpers ────────────────────────


def _media_type(p: Path) -> str:
    return _MEDIA.get(p.suffix.lower(), "application/octet-stream")


def _read_pointer(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_pointer(p: Path, value: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding="utf-8")


def _list_assets(
    kinds_dirs: list[tuple[str, Path]], exts: set[str], active: str, first: str | None = None
) -> list[dict]:
    items: list[dict] = []
    for kind, d in kinds_dirs:
        if not d.is_dir():
            continue
        # `first` (e.g. the white card) is pinned to the top of its kind;
        # everything else is alphabetical.
        ordered = sorted(d.iterdir(), key=lambda f: (f.name != first, f.name.lower()))
        for f in ordered:
            if f.is_file() and f.suffix.lower() in exts:
                ref = f"{kind}/{f.name}"
                items.append(
                    {
                        "kind": kind,
                        "name": f.name,
                        "active": ref == active,
                        "size_bytes": f.stat().st_size,
                    }
                )
    return items


# ───────────────────── letterhead gallery ─────────────────


@router.get("/letterheads")
def list_letterheads():
    """All letterheads — bundled built-ins (white first) then operator uploads."""
    _ensure_active_letterhead()
    active = _read_pointer(_active_letterhead_file())
    items = _list_assets(
        [("default", _letterhead_dir("default")), ("upload", _letterhead_dir("upload"))],
        _IMAGE_EXTS,
        active,
        first=_WHITE_LETTERHEAD,
    )
    return {"items": items}


@router.get("/letterheads/{kind}/{name}")
def get_letterhead_file(kind: str, name: str):
    p = _letterhead_dir(kind) / _safe_name(name)
    if not p.is_file() or p.suffix.lower() not in _IMAGE_EXTS:
        raise HTTPException(
            404,
            detail={"error_code": "not_found", "message": f"no letterhead {kind}/{name}"},
        )
    return FileResponse(str(p), media_type=_media_type(p))


@router.post("/letterheads/{kind}/{name}/activate")
def activate_letterhead(kind: str, name: str):
    _activate_letterhead(kind, _safe_name(name))
    return {"ok": True, "kind": kind, "name": name}


@router.delete("/letterheads/{kind}/{name}", status_code=204)
def delete_letterhead_file(kind: str, name: str):
    safe = _safe_name(name)
    if kind != "upload":
        raise HTTPException(
            400,
            detail={
                "error_code": "not_deletable",
                "message": "only uploaded letterheads can be deleted",
            },
        )
    (printer_uploads_dir() / safe).unlink(missing_ok=True)
    # If we just deleted the active letterhead, fall back to the white card.
    if _read_pointer(_active_letterhead_file()) == f"upload/{safe}":
        _active_letterhead_file().unlink(missing_ok=True)
        _letterhead_path().unlink(missing_ok=True)
        _ensure_active_letterhead()
    return None


def _activate_letterhead(kind: str, name: str) -> None:
    """Copy the chosen image into the active slot so print/compose — and the
    printer service's path check — only ever deal with one in-data-dir file."""
    src = _letterhead_dir(kind) / name
    if not src.is_file() or src.suffix.lower() not in _IMAGE_EXTS:
        raise HTTPException(
            404,
            detail={"error_code": "not_found", "message": f"no letterhead {kind}/{name}"},
        )
    _atomic_write(_letterhead_path(), src.read_bytes())
    _write_pointer(_active_letterhead_file(), f"{kind}/{name}")


# The bundled plain-white card — the always-available fallback. Composing onto
# it is effectively a plain-text print, so compose/print never dead-end.
_WHITE_LETTERHEAD = "white.png"


def _ensure_active_letterhead() -> None:
    """Guarantee an active letterhead exists, falling back to the white card.
    Keeps compose/print working on a fresh install and after the active
    upload is deleted — there is always something to draw on."""
    if _letterhead_path().exists():
        return
    if (printer_default_letterheads_dir() / _WHITE_LETTERHEAD).is_file():
        _activate_letterhead("default", _WHITE_LETTERHEAD)


@router.post("/letterhead")
async def upload_letterhead(file: Annotated[UploadFile, File()]):
    # TODO(auth): unauthenticated upload — gate this before the control API is
    # reachable beyond the local network.
    if file.content_type not in _ALLOWED_IMAGE_MIME:
        raise HTTPException(
            400,
            detail={
                "error_code": "bad_image_type",
                "message": f"unsupported content-type {file.content_type!r}; need png/jpeg/webp",
            },
        )
    data = await file.read()
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            413,
            detail={
                "error_code": "image_too_large",
                "message": f"image > {_MAX_IMAGE_BYTES} bytes",
            },
        )
    # A client can lie about Content-Type — confirm the bytes really decode as
    # an image before we store them.
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").lower()
        img.verify()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise HTTPException(
            400,
            detail={
                "error_code": "bad_image_bytes",
                "message": f"file is not a decodable image: {e}",
            },
        ) from e
    ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}.get(fmt, ".png")
    name = _sanitize_upload_name(file.filename, "letterhead", ext)
    _atomic_write(printer_uploads_dir() / name, data)
    _activate_letterhead("upload", name)
    return {"ok": True, "kind": "upload", "name": name, "size_bytes": len(data)}


@router.get("/letterhead")
def get_active_letterhead():
    """Serve the currently-active letterhead (the print/compose source)."""
    _ensure_active_letterhead()
    p = _letterhead_path()
    if not p.exists():
        raise HTTPException(
            404, detail={"error_code": "no_letterhead", "message": "no letterhead active"}
        )
    return FileResponse(str(p), media_type="image/png")


# ─────────────────────── font gallery ─────────────────────


@router.get("/fonts")
def list_fonts():
    """All fonts — bundled defaults followed by operator uploads."""
    active = _read_pointer(_active_font_file())
    items = _list_assets(
        [("default", _font_dir("default")), ("upload", _font_dir("upload"))],
        _FONT_EXTS,
        active,
    )
    return {"items": items}


@router.get("/fonts/{kind}/{name}")
def get_font_file(kind: str, name: str):
    p = _font_dir(kind) / _safe_name(name)
    if not p.is_file() or p.suffix.lower() not in _FONT_EXTS:
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"no font {kind}/{name}"}
        )
    return FileResponse(str(p), media_type=_media_type(p))


@router.post("/fonts/{kind}/{name}/activate")
def activate_font(kind: str, name: str):
    safe = _safe_name(name)
    if not (_font_dir(kind) / safe).is_file():
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"no font {kind}/{name}"}
        )
    _write_pointer(_active_font_file(), f"{kind}/{safe}")
    return {"ok": True, "kind": kind, "name": name}


@router.delete("/fonts/{kind}/{name}", status_code=204)
def delete_font_file(kind: str, name: str):
    safe = _safe_name(name)
    if kind != "upload":
        raise HTTPException(
            400,
            detail={
                "error_code": "not_deletable",
                "message": "only uploaded fonts can be deleted",
            },
        )
    (printer_fonts_upload_dir() / safe).unlink(missing_ok=True)
    if _read_pointer(_active_font_file()) == f"upload/{safe}":
        _active_font_file().unlink(missing_ok=True)
    return None


@router.post("/font")
async def upload_font(file: Annotated[UploadFile, File()]):
    # TODO(auth): unauthenticated upload — see upload_letterhead.
    data = await file.read()
    if len(data) > _MAX_FONT_BYTES:
        raise HTTPException(
            413,
            detail={"error_code": "font_too_large", "message": f"font > {_MAX_FONT_BYTES} bytes"},
        )
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _FONT_EXTS:
        raise HTTPException(
            400,
            detail={"error_code": "bad_font_type", "message": "need a .ttf or .otf font"},
        )
    # Confirm the bytes are a font PIL can actually render with.
    from PIL import ImageFont

    try:
        ImageFont.truetype(io.BytesIO(data), 16)
    except OSError as e:
        raise HTTPException(
            400,
            detail={"error_code": "bad_font_bytes", "message": f"file is not a usable font: {e}"},
        ) from e
    name = _sanitize_upload_name(file.filename, "font", ext)
    _atomic_write(printer_fonts_upload_dir() / name, data)
    _write_pointer(_active_font_file(), f"upload/{name}")
    return {"ok": True, "kind": "upload", "name": name, "size_bytes": len(data)}


# ──────────────────────── print intents ───────────────────


@router.post("/print_letterhead", status_code=202)
def print_letterhead(req: Request):
    """Trigger a one-shot print of the currently active letterhead."""
    _ensure_active_letterhead()
    p = _letterhead_path()
    req.app.state.nats_publish(
        "printer.intent.print_file", {"path": str(p), "title": "lafufu letterhead"}
    )
    return {"ok": True, "path": str(p)}


class ComposeReq(BaseModel):
    # Matches DB column cap; prevents oversized PIL composite.
    text: str = Field(max_length=4000)
    lucky_subway_stop: str | None = None
    lucky_numbers: list[int] | None = None


@router.post("/compose", status_code=202)
def compose_print(body: ComposeReq, req: Request):
    """Composite text onto the active letterhead with the active font and queue
    it for printing. With the white card active this is just a plain-text
    print. PIL composition happens in the printer service."""
    _ensure_active_letterhead()
    p = _letterhead_path()
    active_font = _read_pointer(_active_font_file())
    font_name = active_font.split("/", 1)[-1] if active_font else None
    req.app.state.nats_publish(
        "printer.intent.compose",
        {
            "letterhead_path": str(p),
            "text": body.text,
            "lucky_subway_stop": body.lucky_subway_stop,
            "lucky_numbers": body.lucky_numbers,
            "title": "lafufu fortune",
            "font": font_name,
        },
    )
    return {"ok": True}


@router.post("/test_print", status_code=202)
def test_print(req: Request):
    """Print a calibration grid so the operator can see exactly where the
    printer is placing content. Half-inch grid with labeled axes; the four
    corners and center are marked. Regenerated on demand so it always
    matches the current page metrics."""
    grid_path = _data_dir() / "calibration_grid.png"
    _data_dir().mkdir(parents=True, exist_ok=True)
    _generate_calibration_grid(grid_path)
    req.app.state.nats_publish(
        "printer.intent.print_file", {"path": str(grid_path), "title": "lafufu calibration"}
    )
    return {"ok": True, "path": str(grid_path)}


def _generate_calibration_grid(out_path: Path) -> None:
    """Build a simple PNG calibration grid using only stdlib + PIL."""
    # 600x900 px is roughly fine for any common media since fit-to-page
    # handles the rest. Half-inch grid lines, cross at center.
    from PIL import Image, ImageDraw, ImageFont

    W, H = 600, 900
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    # Half-inch grid (60px @ 600x900, treating 120px/inch).
    step = 60
    for x in range(0, W + 1, step):
        d.line([(x, 0), (x, H)], fill=(220, 220, 220), width=1)
    for y in range(0, H + 1, step):
        d.line([(0, y), (W, y)], fill=(220, 220, 220), width=1)
    # Heavier inch lines.
    for x in range(0, W + 1, step * 2):
        d.line([(x, 0), (x, H)], fill=(160, 160, 160), width=2)
    for y in range(0, H + 1, step * 2):
        d.line([(0, y), (W, y)], fill=(160, 160, 160), width=2)
    # Center crosshair.
    d.line([(W // 2, 0), (W // 2, H)], fill=(220, 80, 80), width=2)
    d.line([(0, H // 2), (W, H // 2)], fill=(220, 80, 80), width=2)
    # Corner markers — 30px L-brackets.
    L = 30
    for cx, cy in ((0, 0), (W, 0), (0, H), (W, H)):
        sx = -1 if cx == W else 1
        sy = -1 if cy == H else 1
        d.line([(cx, cy), (cx + sx * L, cy)], fill="black", width=4)
        d.line([(cx, cy), (cx, cy + sy * L)], fill="black", width=4)
    # Labels.
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    d.text((10, H // 2 + 8), "CENTER", fill="red", font=font)
    d.text((10, 10), "TOP-LEFT", fill="black", font=font)
    d.text((W - 130, H - 30), "BOT-RIGHT", fill="black", font=font)
    d.text((W // 2 - 60, 40), "lafufu calibration", fill=(80, 80, 80), font=font)
    img.save(out_path, "PNG")
