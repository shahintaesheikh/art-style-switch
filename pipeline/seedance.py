"""Module 7 — stylized video generation via Seedance 2.0 (plan §11)."""

import base64
import os
import time

import requests

from .prompts import SEEDANCE_PROMPT

SEEDANCE_MODEL = "dreamina-seedance-2-5-260628"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['ARK_API_KEY']}",
            "Content-Type": "application/json"}


def _b64_data_uri(path: str) -> str:
    """Read a local image file and return a base64 data URI."""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def submit_task(style_ref_path: str, kf_urls: list[str], video_uri: str,
                duration: int = 8, seed: int = 42,
                resolution: str | None = None,
                prompt: str | None = None) -> str:
    """Submit the Seedance task; returns task_id.

    style_ref_path: Local path to the reference style image (JPEG).
                     Sent as a base64 data URI — no asset upload needed.
    kf_urls: ModelArk-hosted Seedream output URLs (QC-passed keyframes).
    video_uri: asset:// URI or https URL for the reference video.
    resolution: e.g. "720p", "1080p", "4K" (plan §14).
    prompt: optional override for SEEDANCE_PROMPT (e.g. from LLM gate).
    """
    prompt_text = prompt if prompt is not None else SEEDANCE_PROMPT
    style_b64 = _b64_data_uri(style_ref_path)
    content = [{"type": "text", "text": prompt_text}]
    content.append({"type": "image_url", "image_url": {"url": style_b64},
                    "role": "reference_image"})
    for u in kf_urls:
        content.append({"type": "image_url", "image_url": {"url": u},
                        "role": "reference_image"})
    content.append({"type": "video_url", "video_url": {"url": video_uri},
                    "role": "reference_video"})
    # Seedance 2.5: when task type is 'video editing', ratio must be 'adaptive' and duration -1
    is_v25 = SEEDANCE_MODEL >= "dreamina-seedance-2-5"
    body = {"model": SEEDANCE_MODEL, "content": content,
            "ratio": "adaptive" if is_v25 else "3:4",
            "duration": -1 if is_v25 else duration,
            "seed": seed,
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
