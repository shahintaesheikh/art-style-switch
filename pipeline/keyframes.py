"""Module 5 — anchor keyframe extraction (plan §9)."""

import math
import subprocess


def anchor_timestamps(duration: float) -> list[int]:
    """5 anchor timestamps at 0%, 25%, 50%, 75%, 100% of duration.

    For the 8.04 s demo clip: [0, 2, 4, 6, 8].
    """
    return [0, math.floor(duration * 0.25), math.floor(duration * 0.50),
            math.floor(duration * 0.75), math.floor(duration * 1.0)]


def extract_frame(video_path: str, ts: int, out_path: str) -> str:
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out_path,
    ], check=True)
    return out_path
