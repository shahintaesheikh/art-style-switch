"""Module 3 — audio extraction (plan §7)."""

import json
import subprocess


def has_audio_stream(video_path: str) -> bool:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type", "-of", "json", video_path,
    ])
    return bool(json.loads(out)["streams"])


def extract_audio(video_path: str, out_path: str) -> str | None:
    """Stream-copy the audio track to out_path. Returns None if the video has no audio."""
    if not has_audio_stream(video_path):
        return None
    subprocess.check_call([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-c:a", "copy", out_path,
    ])
    return out_path
