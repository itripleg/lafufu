"""Generate lafufu base image variants for style selection.

Three modes:
  full     — full character, with filigree (original style)
  nofil    — full character, plain parchment, no filigree or background clutter
  face     — extreme face close-up filling the card (eyes / nose / mouth only)

Usage:
    python scripts/gen_lafufu_base_variants.py --mode nofil --n 10
    python scripts/gen_lafufu_base_variants.py --mode face  --n 6
    python scripts/gen_lafufu_base_variants.py --mode full  --n 10 --denoise 0.40
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

try:
    from PIL import Image
    import io
except ImportError:
    raise SystemExit("Pillow required: uv pip install pillow")

COMFY   = "http://localhost:8188"
REF_SRC = Path("assets/images/letterheads/cardEmpty.png")

UNET = "anima-preview3-base.safetensors"
CLIP = "qwen_3_06b_base.safetensors"
VAE  = "qwen_image_vae.safetensors"

STEPS     = 15
CFG       = 4.0
SAMPLER   = "er_sde"
SCHEDULER = "simple"

SEEDS = [
    1111111, 2345678, 3141592, 4242424, 5555555,
    6660666, 7654321, 8008135, 9191919, 1234567,
    1357913, 2468024,
]

NEG_BASE = (
    "worst quality, low quality, blurry, jpeg artifacts, color, colorful, "
    "realistic, photo, 3d render, multiple characters, text, watermark, "
    "signature, extra limbs"
)

MODES = {
    "full": {
        "out_dir": Path("assets/images/sprites/base_variants"),
        "denoise": 0.40,
        "crop": "char",   # top ~42% of the portrait
        "pos": (
            "masterpiece, best quality, black and white ink illustration, "
            "vintage lowbrow tattoo-flash line art, monochrome linework on aged cream "
            "parchment paper, a small round creature covered in dense shaggy fur, "
            "short rounded furry rabbit ears, pale round moon-face, "
            "big round eyes with four-point star sparkle pupils, "
            "wide toothy grinning mouth, two small paws gripping a ledge, "
            "symmetrical filigree vines, scattered stars, crescent moon, "
            "centered, clean hatched linework, lowbrow aesthetic"
        ),
        "neg": NEG_BASE + ", closed mouth, sad, angry",
    },
    "nofil": {
        "out_dir": Path("assets/images/sprites/base_nofil"),
        "denoise": 0.40,
        "crop": "char",
        "pos": (
            "masterpiece, best quality, black and white ink illustration, "
            "vintage lowbrow tattoo-flash line art, monochrome linework on aged cream "
            "parchment paper, a small round creature covered in dense shaggy fur, "
            "short rounded furry rabbit ears with inner detail, pale round moon-face, "
            "big round eyes with four-point star sparkle pupils, "
            "wide toothy grinning mouth showing jagged teeth, "
            "two small paws resting below, plain empty parchment background, "
            "centered, clean confident hatched linework, lowbrow aesthetic"
        ),
        "neg": NEG_BASE + (
            ", closed mouth, sad, angry, filigree, vines, leaves, flowers, "
            "decorative border, ornament, frame, crescent moon, stars, "
            "scrollwork, foliage, busy background"
        ),
    },
    "face": {
        "out_dir": Path("assets/images/sprites/base_face"),
        "denoise": 0.45,
        "crop": "face",
        "pos": (
            "masterpiece, best quality, black and white ink illustration, "
            "vintage lowbrow tattoo-flash line art, monochrome linework on aged cream "
            "parchment paper, extreme close-up portrait of a small furry creature, "
            "face filling the entire frame, dense shaggy fur, "
            "short rounded furry rabbit ears at the top, "
            "very large round eyes with four-point star sparkle pupils, "
            "wide toothy grinning mouth with jagged teeth, "
            "plain parchment background, centered, "
            "clean bold hatched linework, lowbrow aesthetic"
        ),
        "neg": NEG_BASE + (
            ", closed mouth, sad, angry, body, paws, ledge, "
            "filigree, vines, leaves, flowers, decorative border, ornament, "
            "crescent moon, stars, scrollwork, foliage, full body, far away, small"
        ),
    },
    "faceonly": {
        "out_dir": Path("assets/images/sprites/base_faceonly"),
        "denoise": 0.50,
        "crop": "faceonly",
        "pos": (
            "masterpiece, best quality, black and white ink illustration, "
            "vintage lowbrow tattoo-flash line art, monochrome linework on aged cream "
            "parchment paper, extreme macro close-up of a creature face, "
            "only eyes nose and mouth visible, face fills entire frame edge to edge, "
            "no ears no body, very large expressive round eyes with star-shaped pupils, "
            "wide grinning mouth with jagged teeth, dense fur texture around face, "
            "plain parchment background, centered, "
            "clean bold hatched linework, lowbrow aesthetic"
        ),
        "neg": NEG_BASE + (
            ", ears, body, paws, ledge, full body, far away, small, "
            "filigree, vines, leaves, flowers, decorative border, ornament, "
            "crescent moon, stars, scrollwork, foliage"
        ),
    },
}


# ── image helpers ────────────────────────────────────────────────────────────

def crop_char(src: Path) -> bytes:
    """Top ~42% of the portrait, centred horizontally → character + filigree."""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    side = min(w, int(h * 0.42))
    left = (w - side) // 2
    cropped = img.crop((left, 0, left + side, side)).resize((768, 768), Image.LANCZOS)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def crop_face(src: Path) -> bytes:
    """Tight crop of just the head/face region (includes ears)."""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    # Face sits roughly in the top 28% of the image, centred.
    face_h = int(h * 0.28)
    side   = min(w, face_h)
    left   = (w - side) // 2
    cropped = img.crop((left, 0, left + side, side)).resize((768, 768), Image.LANCZOS)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def crop_faceonly(src: Path) -> bytes:
    """Very tight crop of just the eyes/nose/mouth — skips the ears entirely."""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    # The face features (below the ears) sit roughly between 10% and 27% of image height.
    top    = int(h * 0.10)
    bottom = int(h * 0.27)
    zone_h = bottom - top
    side   = min(w, zone_h)
    left   = (w - side) // 2
    cropped = img.crop((left, top, left + side, bottom)).resize((768, 768), Image.LANCZOS)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def upload_image(data: bytes, name: str) -> str:
    boundary = "----lafufu" + uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'.encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += data
    body += f"\r\n--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        COMFY + "/upload/image", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        COMFY + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def build_graph(ref_name: str, seed: int, denoise: float, pos: str, neg: str) -> dict:
    return {
        "1":  {"class_type": "UNETLoader",
               "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2":  {"class_type": "CLIPLoader",
               "inputs": {"clip_name": CLIP, "type": "stable_diffusion"}},
        "3":  {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4":  {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "5":  {"class_type": "VAEEncode",
               "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "6":  {"class_type": "CLIPTextEncode",
               "inputs": {"text": pos, "clip": ["2", 0]}},
        "7":  {"class_type": "CLIPTextEncode",
               "inputs": {"text": neg, "clip": ["2", 0]}},
        "8":  {"class_type": "KSampler",
               "inputs": {"model": ["1", 0], "positive": ["6", 0],
                          "negative": ["7", 0], "latent_image": ["5", 0],
                          "seed": seed, "steps": STEPS, "cfg": CFG,
                          "sampler_name": SAMPLER, "scheduler": SCHEDULER,
                          "denoise": denoise}},
        "9":  {"class_type": "VAEDecode",
               "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0],
                          "filename_prefix": f"lafufu_base_{seed}"}},
    }


def run_one(ref_name: str, seed: int, denoise: float,
            pos: str, neg: str, out_dir: Path, idx: int) -> Path:
    graph = build_graph(ref_name, seed, denoise, pos, neg)
    pid = post("/prompt", {"prompt": graph})["prompt_id"]
    print(f"  [{idx:02d}] seed={seed}  queued {pid} ...", flush=True)
    while True:
        with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=30) as r:
            hist = json.loads(r.read())
        if pid in hist:
            break
        time.sleep(2)
    outs = hist[pid]["outputs"]
    img = next(v["images"][0] for v in outs.values() if v.get("images"))
    q = urllib.parse.urlencode({
        "filename": img["filename"],
        "subfolder": img.get("subfolder", ""),
        "type": img.get("type", "output"),
    })
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"base_{idx:02d}_s{seed}.png"
    with urllib.request.urlopen(f"{COMFY}/view?{q}", timeout=30) as r:
        dest.write_bytes(r.read())
    print(f"  [{idx:02d}] saved -> {dest}", flush=True)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode",    choices=list(MODES), default="nofil")
    ap.add_argument("--n",       type=int,   default=10)
    ap.add_argument("--denoise", type=float, default=None,
                    help="override default denoise for this mode")
    args = ap.parse_args()

    cfg = MODES[args.mode]
    denoise  = args.denoise if args.denoise is not None else cfg["denoise"]
    out_dir  = cfg["out_dir"]
    seeds    = SEEDS[: args.n]

    print(f"Mode: {args.mode}  denoise={denoise}  steps={STEPS}", flush=True)

    crop_fn = {"face": crop_face, "faceonly": crop_faceonly}.get(cfg["crop"], crop_char)
    crop_name = f"lafufu_crop_{args.mode}.png"
    print(f"Cropping reference ({cfg['crop']}) from {REF_SRC} ...", flush=True)
    crop_bytes = crop_fn(REF_SRC)
    Path(crop_name).write_bytes(crop_bytes)
    print(f"Crop saved to {crop_name}", flush=True)

    ref_name = upload_image(crop_bytes, crop_name)
    print(f"Uploaded as {ref_name!r}", flush=True)
    print(f"Generating {len(seeds)} variants -> {out_dir}/\n", flush=True)

    for i, seed in enumerate(seeds, 1):
        run_one(ref_name, seed, denoise, cfg["pos"], cfg["neg"], out_dir, i)

    print(f"\nDone — {len(seeds)} images in {out_dir}/")


if __name__ == "__main__":
    main()
