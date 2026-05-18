"""Printer assets: upload + serve the letterhead image used when printing."""

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

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
