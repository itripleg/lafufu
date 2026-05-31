"""Generic image library — bucket-aware upload/list/serve/delete used by both
the printer letterhead gallery and the animation sprite gallery.

Supported buckets: ``"letterheads"`` and ``"sprites"`` (see :data:`BUCKETS`).
"""

import io
import os
import re
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from lafufu_shared.paths import (
    image_letterheads_dir,
    image_sprites_defaults_dir,
    image_sprites_dir,
    printer_default_letterheads_dir,
)

# ───────────────────────── constants ──────────────────────────

ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
# Video — single-media emotion clips on the pet screen (sprites bucket only in
# practice). Larger cap; validated by content-type + extension, not PIL.
ALLOWED_VIDEO_MIME = {"video/mp4"}
VIDEO_EXTS = {".mp4"}
MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50 MB
# Everything the library will list/serve (images + video).
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
BUCKETS = ("letterheads", "sprites")
_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
}


# ───────────────────────── helpers ──────────────────────────


def media_type(p: Path) -> str:
    return _MEDIA.get(p.suffix.lower(), "application/octet-stream")


def safe_name(name: str) -> str:
    """Reject anything that isn't a bare filename — blocks path traversal.

    Path separators are checked explicitly: a backslash is a legal filename
    character on Linux, so `Path(name).name` alone wouldn't catch `a\\b`
    there. Names only ever come from bundled defaults or sanitized uploads,
    so neither separator should appear legitimately."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or name != Path(name).name:
        raise HTTPException(
            400, detail={"error_code": "bad_name", "message": f"invalid name {name!r}"}
        )
    return name


def sanitize_upload_name(raw: str | None, default_stem: str, ext: str) -> str:
    """Turn an arbitrary client-supplied filename into a safe one with `ext`."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(raw or "").stem).strip("-._")
    if not stem:
        stem = f"{default_stem}-{int(time.time())}"
    return f"{stem}{ext}"


def atomic_write(target: Path, data: bytes) -> None:
    """Write via a sibling tmp file + os.replace so a crash mid-write can't
    leave a half-written asset behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def bucket_dir(bucket: str, kind: str) -> Path:
    """Resolve a (bucket, kind) pair to an absolute directory path.

    Raises :class:`fastapi.HTTPException` 404 for unknown bucket or kind."""
    if bucket == "letterheads":
        if kind == "default":
            return printer_default_letterheads_dir()
        if kind == "upload":
            return image_letterheads_dir()
        raise HTTPException(
            404, detail={"error_code": "bad_kind", "message": f"unknown kind {kind!r}"}
        )
    if bucket == "sprites":
        if kind == "default":
            return image_sprites_defaults_dir()
        if kind == "upload":
            return image_sprites_dir()
        raise HTTPException(
            404, detail={"error_code": "bad_kind", "message": f"unknown kind {kind!r}"}
        )
    raise HTTPException(
        404, detail={"error_code": "bad_bucket", "message": f"unknown bucket {bucket!r}"}
    )


# ───────────────────────── router ──────────────────────────

router = APIRouter()


@router.get("/images/{bucket}")
def list_bucket(bucket: str):
    if bucket not in BUCKETS:
        raise HTTPException(
            404, detail={"error_code": "bad_bucket", "message": f"unknown bucket {bucket!r}"}
        )
    items = []
    for kind in ("default", "upload"):
        d = bucket_dir(bucket, kind)
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file() and f.suffix.lower() in MEDIA_EXTS:
                items.append({"kind": kind, "name": f.name, "size_bytes": f.stat().st_size})
    return {"items": items}


@router.get("/images/{bucket}/{kind}/{name}")
def get_file(bucket: str, kind: str, name: str):
    p = bucket_dir(bucket, kind) / safe_name(name)
    if not p.is_file() or p.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(
            404,
            detail={"error_code": "not_found", "message": f"no media {bucket}/{kind}/{name}"},
        )
    return FileResponse(str(p), media_type=media_type(p))


@router.post("/images/{bucket}/upload")
async def upload(bucket: str, file: Annotated[UploadFile, File()]):
    if bucket not in BUCKETS:
        raise HTTPException(
            404, detail={"error_code": "bad_bucket", "message": f"unknown bucket {bucket!r}"}
        )
    is_video = file.content_type in ALLOWED_VIDEO_MIME
    if file.content_type not in ALLOWED_IMAGE_MIME and not is_video:
        raise HTTPException(
            400,
            detail={
                "error_code": "bad_media_type",
                "message": (
                    f"unsupported content-type {file.content_type!r}; need png/jpeg/webp or mp4"
                ),
            },
        )
    data = await file.read()
    cap = MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES
    if len(data) > cap:
        raise HTTPException(
            413,
            detail={
                "error_code": "media_too_large",
                "message": f"{'video' if is_video else 'image'} > {cap} bytes",
            },
        )

    if is_video:
        # No PIL for video — sniff the ISO-BMFF/MP4 'ftyp' box at offset 4 so a
        # mislabeled file can't smuggle in arbitrary bytes. Cheap and dependency-free.
        if len(data) < 12 or data[4:8] != b"ftyp":
            raise HTTPException(
                400,
                detail={
                    "error_code": "bad_video_bytes",
                    "message": "file is not a valid mp4 (missing ftyp box)",
                },
            )
        name = sanitize_upload_name(file.filename, bucket.rstrip("s"), ".mp4")
        atomic_write(bucket_dir(bucket, "upload") / name, data)
        return {"ok": True, "kind": "upload", "name": name, "size_bytes": len(data)}

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
    name = sanitize_upload_name(file.filename, bucket.rstrip("s"), ext)  # "letterhead" / "sprite"
    atomic_write(bucket_dir(bucket, "upload") / name, data)
    return {"ok": True, "kind": "upload", "name": name, "size_bytes": len(data)}


@router.delete("/images/{bucket}/upload/{name}", status_code=204)
def delete_upload(bucket: str, name: str):
    (bucket_dir(bucket, "upload") / safe_name(name)).unlink(missing_ok=True)
    return None
