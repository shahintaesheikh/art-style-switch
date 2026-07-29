"""Module 1 — probe input video with ffprobe (plan §5)."""

import json
import subprocess


def probe(path: str) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration",
        "-of", "json", path,
    ])
    d = json.loads(out)
    s = d["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {
        "width": s["width"],
        "height": s["height"],
        "fps": float(num) / float(den),
        "duration": float(s.get("duration") or d["format"]["duration"]),
    }
