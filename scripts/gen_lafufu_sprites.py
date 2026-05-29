"""Generate lafufu emotion sprites via the ComfyUI 'Anima' model (img2img).

Reference-conditioned: VAE-encodes a cropped square of the canonical lafufu
letterhead art, partially denoises with the Anima base model, and varies only
the expression clause per sprite (fixed seed => consistent creature).

Anima is Qwen-Image-based; no ControlNet/IP-Adapter models are installed, so
img2img is the reference path that actually binds to this checkpoint.

Usage:
    python scripts/gen_lafufu_sprites.py --only neutral surprised --denoise 0.6
    python scripts/gen_lafufu_sprites.py --all --denoise 0.6
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

COMFY = "http://localhost:8188"
REF_CROP = Path("lafufu_ref_crop.png")
OUT_DIR = Path("assets/images/sprites")

UNET = "anima-preview3-base.safetensors"
CLIP = "qwen_3_06b_base.safetensors"
VAE = "qwen_image_vae.safetensors"

SEED = 7777777
STEPS = 30
CFG = 4.0
SAMPLER = "er_sde"
SCHEDULER = "simple"

POS_BASE = (
    "masterpiece, best quality, black and white ink illustration, "
    "vintage lowbrow tattoo-flash line art, monochrome linework on aged cream "
    "parchment paper, a small furry creature with tall rabbit ears, pale round "
    "face, big round eyes with four-point star sparkle pupils, two small paws "
    "resting on a ledge, surrounded by tiny stars, a crescent moon and filigree "
    "vines, centered, clean confident linework, {expr}"
)
NEG = (
    "worst quality, low quality, blurry, jpeg artifacts, sepia, color, colorful, "
    "realistic, photo, 3d render, multiple characters, frame, border, text, "
    "watermark, signature, extra limbs"
)

EXPRESSIONS = {
    "neutral": "calm neutral expression, relaxed closed mouth, gentle steady gaze",
    "happy": "happy joyful expression, big cheerful toothy smile, eyes bright and curved with delight",
    "sad": "sad sorrowful expression, downturned mouth, teary droopy eyes, ears drooping down",
    "angry": "angry furious expression, furrowed angry brows, gritted bared teeth, ears pinned back",
    "surprised": "surprised shocked expression, wide round eyes, mouth open in an O shape, ears perked up high",
    "agree": "approving expression, confident warm closed-mouth smile, head nodding, one paw raised thumbs up",
    "disagree": "disapproving expression, frowning, narrowed skeptical eyes, head turned aside, paws crossed",
}


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(COMFY + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def upload_ref() -> str:
    """Upload the reference crop; returns the server-side filename."""
    boundary = "----lafufu" + uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (b'Content-Disposition: form-data; name="image"; '
             b'filename="lafufu_ref_crop.png"\r\n')
    body += b"Content-Type: image/png\r\n\r\n"
    body += REF_CROP.read_bytes()
    body += f"\r\n--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        COMFY + "/upload/image", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]


def build_graph(ref_name: str, emotion: str, denoise: float) -> dict:
    pos = POS_BASE.format(expr=EXPRESSIONS[emotion])
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "stable_diffusion"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "5": {"class_type": "VAEEncode",
              "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": pos, "clip": ["2", 0]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": NEG, "clip": ["2", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["5", 0],
                         "seed": SEED, "steps": STEPS, "cfg": CFG,
                         "sampler_name": SAMPLER, "scheduler": SCHEDULER,
                         "denoise": denoise}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0],
                          "filename_prefix": f"lafufu_{emotion}"}},
    }


def run_one(ref_name: str, emotion: str, denoise: float) -> Path:
    graph = build_graph(ref_name, emotion, denoise)
    pid = post("/prompt", {"prompt": graph})["prompt_id"]
    print(f"[{emotion}] queued {pid} (denoise={denoise}) ...", flush=True)
    while True:
        with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=30) as r:
            hist = json.loads(r.read())
        if pid in hist:
            break
        time.sleep(2)
    outs = hist[pid]["outputs"]
    img = next(v["images"][0] for v in outs.values() if v.get("images"))
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"lafufu_{emotion}.png"
    with urllib.request.urlopen(f"{COMFY}/view?{q}", timeout=30) as r:
        dest.write_bytes(r.read())
    print(f"[{emotion}] saved -> {dest}", flush=True)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=list(EXPRESSIONS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--denoise", type=float, default=0.6)
    args = ap.parse_args()
    todo = list(EXPRESSIONS) if args.all else (args.only or ["neutral"])

    ref_name = upload_ref()
    print(f"uploaded reference as {ref_name!r}", flush=True)
    for emotion in todo:
        run_one(ref_name, emotion, args.denoise)


if __name__ == "__main__":
    main()
