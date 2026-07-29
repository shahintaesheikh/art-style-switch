"""Module 6 — styled keyframe generation via Seedream i2i (plan §10)."""

import base64
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from .prompts import KEYFRAME_PROMPT

# Plan §2.2's doubao-seedream-5-0-lite-260128 is not enabled on this account;
# seedream-5-0-260128 is the available i2i-capable Seedream model.
SEEDREAM_MODEL = "seedream-5-0-260128"


def b64_image(path: str) -> str:
    """Data URI for ModelArk's image[] parameter (seedream-5-0 rejects raw base64)."""
    with open(path, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")


def gen_styled_keyframe(raw_path: str, style_path: str, seed: int) -> str:
    """One Seedream i2i call; returns the ModelArk-hosted URL of the styled keyframe."""
    r = requests.post(
        f"{os.environ['ARK_BASE_URL']}/images/generations",
        headers={"Authorization": f"Bearer {os.environ['ARK_API_KEY']}",
                 "Content-Type": "application/json"},
        json={
            "model": SEEDREAM_MODEL,
            "prompt": KEYFRAME_PROMPT,
            "image": [b64_image(raw_path), b64_image(style_path)],
            "size": "2K",
            "response_format": "url",
            "watermark": False,
            "seed": seed,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["data"][0]["url"]


def gen_styled_keyframes(raw_paths: list[str], style_path: str,
                         seeds: tuple[int, ...] = (42, 43)) -> list[str]:
    """Parallel Seedream i2i for all interior anchors (plan §10.2)."""
    with ThreadPoolExecutor(max_workers=len(raw_paths)) as ex:
        futs = [ex.submit(gen_styled_keyframe, p, style_path, s)
                for p, s in zip(raw_paths, seeds)]
        return [f.result() for f in futs]
