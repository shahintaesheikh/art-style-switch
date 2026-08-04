#!/usr/bin/env python3
"""VLM Gate — grades the generated video on the same 5-dimension rubric
plus medium consistency throughout the video. Uses dola-seed-2-1-turbo-260628
directly via chat completions. If the video fails, caller should regenerate
(up to 2 retries).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import tempfile
from pathlib import Path

import requests

# Corporate TLS interception (SealSuite SWG) — use OS trust store
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VLM_MODEL = "dola-seed-2-1-turbo-260628"
INFERENCE_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# Rubric thresholds — same as the Evaluator, plus medium_consistency
HARD_GATE_MIN = {"geometry": 5, "composition": 5, "medium": 5,
                 "line_quality": 5, "medium_consistency": 5}
SOFT_GATE_MIN = {"palette": 4}

ALL_DIMENSIONS = ("geometry", "composition", "medium", "palette",
                  "line_quality", "medium_consistency")

ANCHOR_IDS = ("A0", "A1", "A2", "A3", "A4")

VLM_GATE_SYSTEM_PROMPT = """You are a video quality gate. You receive frames extracted from a generated video at 5 evenly-spaced timestamps (0%, 25%, 50%, 75%, 100%), plus the text prompt that was used to generate it. Your job: grade the video on 6 dimensions.

## Dimensions (each scored 1-5)

1. geometry: Object count, positions, orientations, and vanishing point match the source video's raw frames 1:1. No added/removed/moved/rotated/resized/morphed objects.
2. composition: Same framing, zoom, and crop across all frames. 3:4 aspect ratio. No letterboxing.
3. medium: Rendering medium matches the target style EXACTLY (pencil + colored-pencil on paper). The degree of application — stroke weight, hatching density, pencil pressure, texture coarseness — must be perceptually indistinguishable from the style reference.
4. palette: Color treatment matches the target style (e.g. hatched crimson vs flat red, graphite-on-cream vs saturated color).
5. line_quality: Mark/edge character matches the target style EXACTLY. Fineness and precision of linework — stroke width, detail richness, how tight or loose the linework is — must match the reference.
6. medium_consistency: The rendering medium is UNIFORM throughout the entire video. No drift or switching between different mediums across the clip. The video must look like it was drawn in one sitting with the same pencil, same pressure, same stroke character from frame 0 to the end. A video that shifts between mediums (even subtly) should score lower.

## Score anchors (apply to every dimension)
- 5 = matches the reference EXACTLY on this dimension; no detectable deviation whatsoever.
- 4 = minor deviation visible only on close inspection. NOTE: 4 is a FAILING score for all dimensions except palette — treat any 4 as a failure.
- 3 = clear deviation or drift; the dimension is recognizably off-target.
- 2 = largely wrong; only traces of the target remain.
- 1 = completely wrong on this dimension.

## Output
Return exactly one JSON object with no prose, no markdown fences, no commentary outside the JSON:
{
  "pass": true/false,
  "scores": {
    "geometry": {"score": 1-5, "rationale": "..."},
    "composition": {"score": 1-5, "rationale": "..."},
    "medium": {"score": 1-5, "rationale": "..."},
    "palette": {"score": 1-5, "rationale": "..."},
    "line_quality": {"score": 1-5, "rationale": "..."},
    "medium_consistency": {"score": 1-5, "rationale": "..."}
  },
  "summary": "brief overall assessment"
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"}


def b64_file(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def extract_frames(video_path: str, timestamps: list[float],
                   tmpdir: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Extract frames from video at given timestamps. Returns list of file paths."""
    frames = []
    for i, ts in enumerate(timestamps):
        out = os.path.join(tmpdir, f"frame_{i}.jpg")
        subprocess.run([
            ffmpeg, "-y", "-ss", str(ts), "-i", video_path,
            "-frames:v", "1", "-q:v", "2", out,
        ], check=True, capture_output=True)
        frames.append(out)
    return frames


# ---------------------------------------------------------------------------
# VLM Gate
# ---------------------------------------------------------------------------

def call_vlm(api_key: str, frames: list[str], seedance_prompt: str,
             timeout: int = 1200) -> dict:
    """Send frames + prompt to the VLM and return the parsed JSON response.

    Uses the chat completions API with the vision model.
    """
    content = [{"type": "text", "text": seedance_prompt}]
    for fp in frames:
        data = b64_file(fp)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{data}"}
        })

    r = requests.post(
        f"{INFERENCE_BASE_URL}/chat/completions",
        headers=_headers(api_key),
        json={
            "model": VLM_MODEL,
            "messages": [
                {"role": "system", "content": VLM_GATE_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "max_tokens": 2048,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    text = body["choices"][0]["message"]["content"]

    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    return json.loads(cleaned)


def scores_pass(scores: dict) -> tuple[bool, list[str]]:
    """Driver-side pass/fail recomputation from thresholds."""
    failures: list[str] = []
    for dim, minimum in {**HARD_GATE_MIN, **SOFT_GATE_MIN}.items():
        if dim not in scores:
            failures.append(dim)
            continue
        if scores[dim]["score"] < minimum:
            failures.append(dim)
    return (not failures, failures)


def grade_video(api_key: str, video_path: str, timestamps: list[float],
                seedance_prompt: str, ffmpeg: str = "ffmpeg",
                attempt: int = 1) -> dict:
    """Run the VLM gate on a generated video.

    Returns {
        "pass": bool,
        "scores": dict,
        "failures": list[str],
        "attempt": int,
        "summary": str,
    }
    """
    with tempfile.TemporaryDirectory(prefix="vlm-") as tmpdir:
        frames = extract_frames(video_path, timestamps, tmpdir, ffmpeg)
        result = call_vlm(api_key, frames, seedance_prompt, timeout=600)
        scores = result.get("scores", {})
        passed, failures = scores_pass(scores)
        return {
            "pass": passed,
            "scores": scores,
            "failures": failures,
            "attempt": attempt,
            "summary": result.get("summary", ""),
        }