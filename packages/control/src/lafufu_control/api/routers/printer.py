"""Printer assets: upload + serve the letterhead image used when printing."""

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter()


def _data_dir() -> Path:
    return Path(os.environ.get("LAFUFU_PRINTER_DATA_DIR", "/srv/lafufu/data/printer"))


def _letterhead_path() -> Path:
    return _data_dir() / "letterhead.png"


_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("/letterhead")
def get_letterhead():
    p = _letterhead_path()
    if not p.exists():
        raise HTTPException(
            404, detail={"error_code": "no_letterhead", "message": "no letterhead uploaded"}
        )
    return FileResponse(str(p), media_type="image/png")


@router.post("/letterhead")
async def upload_letterhead(file: Annotated[UploadFile, File()]):
    if file.content_type not in _ALLOWED_MIME:
        raise HTTPException(
            400,
            detail={
                "error_code": "bad_image_type",
                "message": f"unsupported content-type {file.content_type!r}; need png/jpeg/webp",
            },
        )
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            413,
            detail={"error_code": "image_too_large", "message": f"image > {_MAX_BYTES} bytes"},
        )
    _data_dir().mkdir(parents=True, exist_ok=True)
    _letterhead_path().write_bytes(data)
    return {"ok": True, "size_bytes": len(data)}


@router.delete("/letterhead", status_code=204)
def delete_letterhead():
    p = _letterhead_path()
    if p.exists():
        p.unlink()
    return None


@router.post("/print_letterhead", status_code=202)
def print_letterhead(req: Request):
    """Trigger a one-shot print of the currently uploaded letterhead."""
    p = _letterhead_path()
    if not p.exists():
        raise HTTPException(
            404, detail={"error_code": "no_letterhead", "message": "upload a letterhead first"}
        )
    req.app.state.nats_publish(
        "printer.intent.print_file", {"path": str(p), "title": "lafufu letterhead"}
    )
    return {"ok": True, "path": str(p)}


class ComposeReq(BaseModel):
    text: str
    lucky_subway_stop: str | None = None
    lucky_numbers: list[int] | None = None


@router.post("/compose", status_code=202)
def compose_print(body: ComposeReq, req: Request):
    """Composite text onto the uploaded letterhead and queue it for printing.
    The actual PIL composition happens in the printer service."""
    p = _letterhead_path()
    if not p.exists():
        raise HTTPException(
            404,
            detail={
                "error_code": "no_letterhead",
                "message": "upload a letterhead first — compose draws text on it",
            },
        )
    req.app.state.nats_publish(
        "printer.intent.compose",
        {
            "letterhead_path": str(p),
            "text": body.text,
            "lucky_subway_stop": body.lucky_subway_stop,
            "lucky_numbers": body.lucky_numbers,
            "title": "lafufu fortune",
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
    """Build a simple PNG calibration grid using only stdlib + struct."""
    # 600 dpi-ish at letter — 600x900 px is roughly fine for any common media
    # since fit-to-page handles the rest. Half-inch grid lines, cross at center.
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
    d.text(
        (W // 2 - 60, 40),
        "lafufu calibration",
        fill=(80, 80, 80),
        font=font,
    )
    img.save(out_path, "PNG")
