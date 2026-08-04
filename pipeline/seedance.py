"""Module 7 — stylized video generation via Seedance 2.0 (plan §11)."""

import os
import time

import requests

from .prompts import SEEDANCE_PROMPT

SEEDANCE_MODEL = "dreamina-seedance-2-0-260128"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['ARK_API_KEY']}",
            "Content-Type": "application/json"}


def submit_task(style_ref_uri: str, kf_urls: list[str], video_uri: str,
                duration: int = 8, seed: int = 42,
                resolution: str | None = None) -> str:
    """Submit the Seedance task; returns task_id.

    style_ref_uri and video_uri are asset:// URIs from the ModelArk Asset
    Library (uploaded once via console). kf_urls are ModelArk-hosted
    Seedream output URLs, passed straight through.
    resolution: e.g. "720p", "1080p", "4K" (plan §14).
    """
    content = [{"type": "text", "text": SEEDANCE_PROMPT}]
    content.append({"type": "image_url", "image_url": {"url": style_ref_uri},
                    "role": "reference_image"})
    for u in kf_urls:
        content.append({"type": "image_url", "image_url": {"url": u},
                        "role": "reference_image"})
    content.append({"type": "video_url", "video_url": {"url": video_uri},
                    "role": "reference_video"})
    body = {"model": SEEDANCE_MODEL, "content": content,
            "ratio": "3:4", "duration": duration, "seed": seed,
            "camera_fixed": False, "watermark": False}
    if resolution:
        body["resolution"] = resolution
    r = requests.post(
        f"{os.environ['ARK_BASE_URL']}/contents/generations/tasks",
        headers=_headers(),
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def wait_for_task(task_id: str, poll_interval: int = 10, timeout: int = 1800) -> str:
    """Poll until succeeded; returns the ModelArk-hosted video URL (short-lived, ~24 h)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        g = requests.get(
            f"{os.environ['ARK_BASE_URL']}/contents/generations/tasks/{task_id}",
            headers=_headers(), timeout=30)
        g.raise_for_status()
        d = g.json()
        status = d.get("status")
        print("Seedance:", status, flush=True)
        if status == "succeeded":
            return d["content"]["video_url"]
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Seedance {status}: {d}")
        time.sleep(poll_interval)
    raise TimeoutError(f"Seedance task {task_id} not done within {timeout}s")
