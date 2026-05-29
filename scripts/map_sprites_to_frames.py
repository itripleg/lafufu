"""Map the generated lafufu emotion sprites onto the built-in animator frames.

Sets each built-in Frame's `image` to the matching sprite ref so the Studio
expression preview (and any image-driven view) shows the inked lafufu per
emotion. Sprites live in assets/images/sprites/ => the "default" image kind,
so refs are "sprites/default/lafufu_<emotion>.png".

Idempotent: re-running just re-sends the same PUTs. Requires the control
service on :8080 (it serves /api/animator/frames).

Usage:  python scripts/map_sprites_to_frames.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Override for non-local targets, e.g. LAFUFU_CONTROL_URL=http://localhost:8090
BASE = os.environ.get("LAFUFU_CONTROL_URL", "http://localhost:8080").rstrip("/") + "/api"

# frame name -> emotion sprite stem
FRAME_TO_EMOTION = {
    "happy_a": "happy",
    "happy_b": "happy",
    "sad_a": "sad",
    "sad_b": "sad",
    "angry_a": "angry",
    "angry_b": "angry",
    "surprised_held": "surprised",
    "agree_low": "agree",
    "agree_high": "agree",
    "disagree_left": "disagree",
    "disagree_right": "disagree",
    "idle_calm": "neutral",
    "idle_glance_l": "neutral",
    "idle_glance_r": "neutral",
    "idle_look_up": "neutral",
}


def ref(emotion: str) -> str:
    return f"sprites/default/lafufu_{emotion}.png"


def get_frames() -> dict[str, dict]:
    with urllib.request.urlopen(f"{BASE}/animator/frames", timeout=10) as r:
        items = json.loads(r.read())["items"]
    return {f["name"]: f for f in items}


def put_frame(name: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}/animator/frames/{name}", data=data, method="PUT",
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main() -> None:
    try:
        frames = get_frames()
    except urllib.error.URLError as e:
        sys.exit(f"control service not reachable at {BASE}: {e}\n"
                 f"start it with:  uv run python -m lafufu_control")

    updated, skipped = 0, []
    for name, emotion in FRAME_TO_EMOTION.items():
        f = frames.get(name)
        if f is None:
            skipped.append(name)
            continue
        # Full-replace PUT: resend every field, only swap `image`.
        put_frame(name, {
            "head_lr": f["head_lr"], "head_ud": f["head_ud"],
            "eye": f["eye"], "jaw": f["jaw"], "brow": f["brow"],
            "image": ref(emotion), "description": f.get("description"),
        })
        print(f"  {name:18s} -> {ref(emotion)}")
        updated += 1

    print(f"\nmapped {updated} frames" + (f"; missing: {skipped}" if skipped else ""))


if __name__ == "__main__":
    main()
