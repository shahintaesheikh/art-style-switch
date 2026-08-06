"""Module 5 — anchor keyframe extraction (plan §9)."""

import math
import subprocess


def anchor_timestamps(duration: float) -> list[int]:
    """8 anchor timestamps evenly spaced from 0% to 100% of duration.

    Divides duration into 7 equal segments, producing 8 anchors
    at 0%, ~14.3%, ~28.6%, ~42.9%, ~57.1%, ~71.4%, ~85.7%, 100%.
    """
    n = 8
    return [math.floor(duration * i / (n - 1)) for i in range(n)]


def extract_frame(video_path: str, ts: int, out_path: str) -> str:
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out_path,
    ], check=True)
    return out_path
