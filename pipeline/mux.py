"""Module 8 — audio mux (plan §12)."""

import subprocess


def mux_audio(video_path: str, audio_path: str | None, out_path: str) -> str:
    """Stream-copy the video and mux the extracted audio track.

    audio_path=None (source had no audio) → remux video only.
    """
    if audio_path is None:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-c", "copy", "-movflags", "+faststart", out_path,
        ], check=True)
    else:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest", "-movflags", "+faststart",
            out_path,
        ], check=True)
    return out_path
