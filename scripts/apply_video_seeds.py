"""Push video-frame records and updated emotion expressions to a running server.

Run this against local or VPS after deploying the new sprites:
    python scripts/apply_video_seeds.py
    python scripts/apply_video_seeds.py --base http://2.24.90.246:8090

After a git pull on a machine with an existing DB, the server auto-seeds new
vid_* frames on startup. Only run this script if you pulled while the server
was already running (it won't restart to pick up seed changes mid-run).
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any

IDLE = {"head_lr": 2063, "head_ud": 3082, "eye": 2045, "jaw": 1728, "brow": 2075}

# Video frames: idle cycle (20) + laugh cycle (13), all at IDLE servo positions.
VIDEO_FRAMES: dict[str, dict[str, Any]] = {
    **{
        f"vid_idle_{i:02d}": {**IDLE, "image": f"sprites/default/idle_{i:02d}.png"}
        for i in range(1, 21)
    },
    **{
        f"vid_laugh_{i:02d}": {**IDLE, "image": f"sprites/default/laugh_{i:02d}.png"}
        for i in range(1, 14)
    },
}

# Emotion expressions using video frame sequences.
# neutral  → single resting star-pupils frame
# happy    → full laugh cycle, looped
# sad      → squinting section of idle, slow loop
# angry    → squinting section of idle, fast loop
# surprised→ wide-eyed opening frames, once
# agree    → bouncy laugh frames, once
# disagree → squinting idle frames, once
EXPRESSIONS: list[dict[str, Any]] = [
    {
        "name": "neutral",
        "playback": "once",
        "default_duration_ms": 500,
        "default_delay_ms": 0,
        "default_easing": "ease-in-out",
        "steps": [{"frame": "vid_idle_01"}],
        "emotion": "neutral",
    },
    {
        "name": "happy",
        "playback": "loop",
        "default_duration_ms": 150,
        "default_delay_ms": 0,
        "default_easing": "linear",
        "steps": [{"frame": f"vid_laugh_{i:02d}"} for i in range(1, 14)],
        "emotion": "happy",
    },
    {
        "name": "sad",
        "playback": "loop",
        "default_duration_ms": 400,
        "default_delay_ms": 0,
        "default_easing": "ease-in-out",
        "steps": [{"frame": f"vid_idle_{i:02d}"} for i in [6, 7, 8, 9, 10, 11]],
        "emotion": "sad",
    },
    {
        "name": "angry",
        "playback": "loop",
        "default_duration_ms": 160,
        "default_delay_ms": 0,
        "default_easing": "linear",
        "steps": [{"frame": f"vid_idle_{i:02d}"} for i in [5, 6, 7, 8, 9, 8, 7, 6]],
        "emotion": "angry",
    },
    {
        "name": "surprised",
        "playback": "once",
        "default_duration_ms": 300,
        "default_delay_ms": 0,
        "default_easing": "ease-out",
        "steps": [{"frame": f"vid_idle_{i:02d}"} for i in [1, 2, 3]],
        "emotion": "surprised",
    },
    {
        "name": "agree",
        "playback": "once",
        "default_duration_ms": 180,
        "default_delay_ms": 0,
        "default_easing": "ease-in-out",
        "steps": [{"frame": f"vid_laugh_{i:02d}"} for i in [1, 2, 3, 2, 1, 2, 3, 2, 1]],
        "emotion": "agree",
    },
    {
        "name": "disagree",
        "playback": "once",
        "default_duration_ms": 180,
        "default_delay_ms": 0,
        "default_easing": "ease-in-out",
        "steps": [{"frame": f"vid_idle_{i:02d}"} for i in [5, 6, 7, 8, 7, 6, 5, 6, 7]],
        "emotion": "disagree",
    },
]


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()) if r.status != 204 else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    args = ap.parse_args()
    base = args.base.rstrip("/") + "/api"

    # 1. Load existing frames and expressions.
    existing_frames = {f["name"] for f in _req("GET", f"{base}/animator/frames")["items"]}
    existing_exprs = {e["name"] for e in _req("GET", f"{base}/animator/expressions")["items"]}

    # 2. Upsert video frames.
    print(f"Upserting {len(VIDEO_FRAMES)} video frames...")
    for name, pose in VIDEO_FRAMES.items():
        body = {"name": name, **pose}
        if name in existing_frames:
            _req("PUT", f"{base}/animator/frames/{name}", body)
            print(f"  updated {name}")
        else:
            _req("POST", f"{base}/animator/frames", body)
            print(f"  created {name}")

    # 3. Upsert emotion expressions.
    print(f"\nUpserting {len(EXPRESSIONS)} expressions...")
    for expr in EXPRESSIONS:
        name = expr["name"]
        # The PUT endpoint expects steps_json not steps, so we translate.
        body = {
            "name": name,
            "playback": expr["playback"],
            "default_duration_ms": expr["default_duration_ms"],
            "default_delay_ms": expr["default_delay_ms"],
            "default_easing": expr["default_easing"],
            "steps": expr["steps"],
            "emotion": expr["emotion"],
        }
        if name in existing_exprs:
            _req("PUT", f"{base}/animator/expressions/{name}", body)
            print(f"  updated {name}")
        else:
            _req("POST", f"{base}/animator/expressions", body)
            print(f"  created {name}")

    print("\nDone.")


if __name__ == "__main__":
    main()
