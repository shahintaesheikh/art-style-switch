"""Module 5 — anchor keyframe extraction (plan §9)."""

import math
import subprocess


def anchor_timestamps(duration: float) -> list[int]:
    """Interior anchors at ~33% / ~66% (A0 is the user style ref at t=0).

    For the 8.04 s demo clip: [2, 5].
    """
    return [math.floor(duration * 0.33), math.floor(duration * 0.66)]


def extract_frame(video_path: str, ts: int, out_path: str) -> str:
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out_path,
    ], check=True)
    return out_path
