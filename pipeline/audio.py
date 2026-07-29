"""Module 3 — audio extraction (plan §7)."""

import json
import subprocess


def has_audio_stream(video_path: str) -> bool:
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type", "-of", "json", video_path,
    ], capture_output=True, text=True, check=True)
    return bool(json.loads(r.stdout)["streams"])


def extract_audio(video_path: str, out_path: str) -> str | None:
    """Stream-copy the audio track to out_path. Returns None if the video has no audio."""
    if not has_audio_stream(video_path):
        return None
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-c:a", "copy", out_path,
    ], check=True)
    return out_path
