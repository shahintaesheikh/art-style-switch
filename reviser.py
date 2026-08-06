#!/usr/bin/env python3
"""Keyframe QC Reviser — BytePlus Managed Agent + local tool-execution driver.

Scope: the REVISER half of the QC gate in .pi/keyframe_gate_plan.md (§1, §5,
§11.3, §12, §21, §22). The Evaluator, round-0 bootstrap, and the outer
evaluate/revise loop live outside this worktree.

What this script does:
  1. Self-bootstraps a minimal Environment + the Reviser Managed Agent
     (doubao-seed-2-1-pro-260628, 3 declarative custom tools, NO built-in
     toolset, optional Seedream prompt-engineering Skill) and caches IDs in
     ~/.config/kf-qc/agents.json (plan §10, reviser keys only; merges without
     clobbering evaluator keys written by qc_gate.py).
  2. Runs ONE reviser round: takes the Evaluator's graded rubric JSON +
     explanations, builds the §11.3 user message, opens a fresh session, polls
     events (2s cursor polling), executes the agent's custom tool calls locally
     (ffmpeg frame extraction, Seedream i2i), enforces the §22 per-session caps,
     and emits the §21 submit_revision delta + merged keyframe set (Seedance-
     ready for the anchors the gate hands off).

API assumptions taken from keyframe_gate_plan.md (custom-tool/event shapes are
not covered by the public Managed Agents docs — verify on first live run):
  - Custom tools declared at agent creation as
    {"type":"custom","name":...,"description":...,"input_schema":{...}} (Q11).
  - Agent tool-call requests appear in GET /api/v3/sessions/{id}/events as
    events whose type contains "custom_tool" (parsed tolerantly; cursor = last
    event id via ?after=<id>) (Q12).
  - Tool results are POSTed as
    {"events":[{"type":"user.custom_tool_result","custom_tool_use_id":...,
    "is_error":bool,"content":[text + optional base64 image blocks]}]} (§12).
  - Image blocks in user.message / tool results use
    {"type":"image","source":{"type":"url"|"base64",...}} (§12 block shape).

Open items pinned at smoke test (plan §17): exact custom-tool event type name,
Seedream i2i strength/guidance params (SEEDREAM_GUIDANCE_SCALE left unset so
unknown params are never sent), and whether the minimal Environment needs
networking.type:"unrestricted" (agents here have no built-in tools, so the
sandbox itself needs no network — created minimal).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

# Corporate TLS interception (SealSuite SWG) re-signs HTTPS with an enterprise
# CA; use the OS trust store (same fix as pipeline/__init__.py).
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

# --------------------------------------------------------------------------
# Constants (keyframe_gate_plan.md §8 — reviser-relevant subset)
# --------------------------------------------------------------------------

VERSION = "0.1.0"
# Plan Q10/Q36 pins doubao-seed-2-1-pro-260628, but it is not enabled on this
# account (verified 2026-07-31); user approved dola-seed-2-1-turbo-260628, the
# same seed-2.1 generation, as the substitute.
MODEL_ID = "dola-seed-2-1-turbo-260628"
# Plan §8 pins doubao-seedream-5-0-lite-260128, but it is not enabled on this
# account (same situation pipeline/seedream.py already hit); user approved
# seedream-5-0-260128, the available i2i-capable Seedream 5.0 model.
SEEDREAM_MODEL_ID = "dola-seedream-5-0-pro-260628"
ASPECT_RATIO = "3:4"
ASPECT_W, ASPECT_H = 3, 4
# Seedream 5.0 on this account requires ≥ 3,686,400 px and rejects "2K"
# (produces square 2048×2048). Explicit 3:4 resolution verified live.
SEEDREAM_SIZE = "1728x2304"
SEEDREAM_RESPONSE_FORMAT = "url"
SEEDREAM_GUIDANCE_SCALE = None  # plan §17: pin after smoke test; omit until then
INFERENCE_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
ASSETS_OPENAPI_HOST = "ark.ap-southeast-1.byteplusapi.com"
ASSETS_REGION = "ap-southeast-1"
AGENTS_CACHE_PATH = "~/.config/kf-qc/agents.json"
REVISER_MAX_TURNS = 8
REVISER_MAX_KF_PER_ANCHOR = 3
REVISER_MAX_KF_TOTAL = 6
REVISER_WALL_CLOCK_SEC = 600
POLL_INTERVAL_SEC = 2
POST_TOOL_WAIT_SEC = 1
INFRA_RETRY_WAIT_SEC = 5
INFRA_MAX_RETRIES = 1
DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024
ASPECT_TOLERANCE = 0.05
FFMPEG_QV = 2
ANCHOR_IDS = ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7")

# Rubric thresholds (plan §3): hard gates must be 5, soft gates must be ≥4.
# medium and palette are now hard gates — style must match the reference exactly.
HARD_GATE_MIN = {"geometry": 5, "composition": 5, "medium": 5, "line_quality": 5}
SOFT_GATE_MIN = {"palette": 4}

# --------------------------------------------------------------------------
# Prompts (module-level constants, plan §45; wording tuned at smoke test §17)
# --------------------------------------------------------------------------

REVISER_SYSTEM_PROMPT = """\
You are the Keyframe Reviser of a two-agent QC gate for style-transfer video \
pipelines. An Evaluator has scored styled keyframes for 8 fixed anchors (A0, \
A1, A2, A3, A4, A5, A6, A7) on a 5-dimension rubric: geometry and composition are hard gates \
(must score 5 — structure and framing must match the raw video frame 1:1); \
medium and line_quality are hard gates (must score exactly 5 — the medium and \
linework character must match the target art style identically); palette is a \
soft gate (must score at least 4). One or more anchors FAILED. Your job: \
produce corrected styled keyframes for the failing anchors ONLY, so the next \
Evaluator round passes.

You have exactly three custom tools and no other capabilities:

1. extract_keyframe(anchor_id, timestamp_sec)
   Re-extracts the raw video frame at a different timestamp. Use it ONLY to \
slightly shift an anchor's base frame when the current raw frame is itself a \
bad base (motion blur, occlusion, scene transition). You cannot add or remove \
anchors. After a successful extraction, later generate_keyframe calls for \
that anchor use the new raw frame and new timestamp.

2. generate_keyframe(anchor_id, timestamp_sec, prompt, negative_prompt, \
force_new_seed)
   Regenerates the styled keyframe for one anchor via Seedream i2i. The \
driver pins the model, aspect ratio (3:4), and all other generation \
parameters. The same seed is reused per anchor across calls unless \
force_new_seed=true — iterate on the PROMPT first; only request a new seed \
when the prompt is right but the sample is unlucky. Engineer prompts with \
the attached Seedream prompt-engineering skill: name the target medium, \
palette, and line quality explicitly, and always demand exact preservation \
of geometry, object count, positions, orientations, framing, and vanishing \
point from the raw frame. Directly address the Evaluator's failed_dimensions \
and suggested_focus. Use negative_prompt to suppress the observed failure \
modes. Always inspect the returned preview image before your next step.

3. submit_revision(regenerated, unchanged_approved_anchor_ids, attempts_used, \
notes)
   Terminator. Call EXACTLY ONCE when every failing anchor has been \
regenerated. `regenerated` carries one entry per regenerated anchor with the \
keyframe_url returned by generate_keyframe, its seed, the prompt used, and a \
concrete changes_made summary. `unchanged_approved_anchor_ids` lists \
previously approved anchors you did NOT touch. NEVER include an approved \
anchor in `regenerated`.

Constraints:
- Never regenerate or alter approved anchors; use them only as cross-anchor \
coherence reference (medium/palette/line consistency across all anchors).
- Budget per session: at most 8 turns, 3 generate_keyframe calls per anchor, \
6 generate_keyframe calls total, 4 minutes wall clock. The driver \
force-closes the session when any cap is hit — diagnose ALL failing anchors \
first, then regenerate efficiently.
- When geometry or composition failed, strengthen the structural-preservation \
demands in your prompt; consider extract_keyframe only if the raw frame \
itself is the problem.
"""

# --------------------------------------------------------------------------
# Custom tool declarations (plan §5 — input_schema, additionalProperties=false)
# --------------------------------------------------------------------------

REVISER_TOOLS = [
    {
        "type": "custom",
        "name": "extract_keyframe",
        "description": (
            "Re-extract the raw video frame for one anchor at a new timestamp. "
            "Use to slightly shift an anchor's base frame when the current raw "
            "frame is a poor base (blur/occlusion/transition). Cannot add or "
            "remove anchors. Subsequent generate_keyframe calls for this anchor "
            "use the new frame."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "anchor_id": {"type": "string", "enum": list(ANCHOR_IDS)},
                "timestamp_sec": {"type": "number", "minimum": 0},
            },
            "required": ["anchor_id", "timestamp_sec"],
            "additionalProperties": False,
        },
    },
    {
        "type": "custom",
        "name": "generate_keyframe",
        "description": (
            "Regenerate the styled keyframe for one anchor via Seedream i2i "
            "using the current raw frame and the style reference. Model, 3:4 "
            "aspect, and other generation params are driver-pinned. Reuses the "
            "anchor's prior seed unless force_new_seed=true. Returns the "
            "keyframe URL, the seed used, and a preview image."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "anchor_id": {"type": "string", "enum": list(ANCHOR_IDS)},
                "timestamp_sec": {"type": "number", "minimum": 0},
                "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
                "negative_prompt": {"type": "string", "maxLength": 4000},
                "force_new_seed": {"type": "boolean"},
            },
            "required": ["anchor_id", "timestamp_sec", "prompt"],
            "additionalProperties": False,
        },
    },
    {
        "type": "custom",
        "name": "submit_revision",
        "description": (
            "Terminator — call exactly once when every failing anchor has been "
            "regenerated. `regenerated` must only contain anchors that failed "
            "the previous Evaluator round; approved anchors go in "
            "unchanged_approved_anchor_ids."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regenerated": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "anchor_id": {"type": "string", "enum": list(ANCHOR_IDS)},
                            "timestamp_sec": {"type": "number", "minimum": 0},
                            "keyframe_url": {"type": "string", "format": "uri"},
                            "prompt_used": {"type": "string"},
                            "seed": {"type": "integer"},
                            "changes_made": {"type": "string", "maxLength": 1000},
                        },
                        "required": [
                            "anchor_id",
                            "timestamp_sec",
                            "keyframe_url",
                            "prompt_used",
                            "seed",
                            "changes_made",
                        ],
                        "additionalProperties": False,
                    },
                },
                "unchanged_approved_anchor_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(ANCHOR_IDS)},
                },
                "attempts_used": {"type": "integer", "minimum": 1},
                "notes": {"type": "string", "maxLength": 2000},
            },
            "required": [
                "regenerated",
                "unchanged_approved_anchor_ids",
                "attempts_used",
                "notes",
            ],
            "additionalProperties": False,
        },
    },
]


# --------------------------------------------------------------------------
# Errors + HTTP helpers (plan §28: retry-once for retryable, fail-fast 4xx)
# --------------------------------------------------------------------------


class InfraError(Exception):
    """Infrastructure failure — never consumes QC attempts (exit code 3)."""

    def __init__(self, stage: str, error: str):
        super().__init__(f"{stage}: {error}")
        self.stage = stage
        self.error = error


def _request(stage: str, method: str, url: str, timeout: int = 120, **kw):
    """requests wrapper: retry once after 5s on 429/5xx/network/JSON errors;
    fail fast on other 4xx. Returns parsed JSON."""
    last_err = None
    for attempt in range(INFRA_MAX_RETRIES + 1):
        try:
            r = requests.request(method, url, timeout=timeout, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}: {r.text[:500]}"
            elif r.status_code >= 400:
                raise InfraError(stage, f"HTTP {r.status_code} (fail-fast): {r.text[:500]}")
            else:
                try:
                    return r.json()
                except ValueError:
                    last_err = f"malformed JSON: {r.text[:300]}"
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = f"network error: {e}"
        if attempt < INFRA_MAX_RETRIES:
            time.sleep(INFRA_RETRY_WAIT_SEC)
    raise InfraError(stage, last_err or "unknown error")


def _ark_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


# --------------------------------------------------------------------------
# Input resolution: local / https:// / asset:// (plan §34, §40)
# --------------------------------------------------------------------------


class Resolved:
    def __init__(self, kind: str, path: Path | None, url: str | None):
        self.kind = kind  # "path" | "url"
        self.path = path
        self.url = url


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _assets_openapi(action: str, body: dict, ak: str, sk: str) -> dict:
    """HMAC-SHA256 v4 signed call to the Assets OpenAPI (assets-api skill)."""
    body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    scope = f"{date_stamp}/{ASSETS_REGION}/ark/request"
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical_headers = (
        f"content-type:application/json\nhost:{ASSETS_OPENAPI_HOST}\n"
        f"x-content-sha256:{body_hash}\nx-date:{x_date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_req = (
        f"POST\n/\nAction={action}&Version=2024-01-01\n"
        f"{canonical_headers}\n{signed_headers}\n{body_hash}"
    )
    string_to_sign = (
        f"HMAC-SHA256\n{x_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_req.encode('utf-8')).hexdigest()}"
    )
    k_signing = _hmac(_hmac(_hmac(_hmac(sk.encode("utf-8"), date_stamp), ASSETS_REGION), "ark"), "request")
    sig = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={sig}"
    resp = requests.post(
        f"https://{ASSETS_OPENAPI_HOST}/?Action={action}&Version=2024-01-01",
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "Host": ASSETS_OPENAPI_HOST,
            "X-Date": x_date,
            "X-Content-Sha256": body_hash,
            "Authorization": auth,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise InfraError("assets_openapi", f"{action} HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def resolve_asset(asset_uri: str, ak: str, sk: str) -> str:
    """asset://<id> → signed 12h download URL (polls GetAsset until Active)."""
    asset_id = asset_uri[len("asset://"):]
    deadline = time.time() + 60
    while True:
        resp = _assets_openapi("GetAsset", {"Id": asset_id}, ak, sk)
        err = resp.get("ResponseMetadata", {}).get("Error")
        if err:
            raise InfraError("resolve_asset", f"GetAsset {asset_id}: {err}")
        result = resp.get("Result", {})
        status = result.get("Status")
        if status == "Active" and result.get("URL"):
            return result["URL"]
        if status == "Failed":
            raise InfraError("resolve_asset", f"asset {asset_id} status=Failed")
        if time.time() > deadline:
            raise InfraError("resolve_asset", f"asset {asset_id} still {status} after 60s")
        time.sleep(3)


def resolve_input(uri_or_path: str, args, tmp: Path, stage: str) -> Resolved:
    """local path / https:// URL / asset:// URI → Resolved (plan §34)."""
    if uri_or_path.startswith("asset://"):
        ak = args.ark_access_key or os.environ.get("ARK_ACCESS_KEY")
        sk = args.ark_secret_key or os.environ.get("ARK_SECRET_KEY")
        if not ak or not sk:
            raise InfraError(stage, "asset:// input requires ARK_ACCESS_KEY/ARK_SECRET_KEY")
        return Resolved("url", None, resolve_asset(uri_or_path, ak, sk))
    if uri_or_path.startswith("https://") or uri_or_path.startswith("http://"):
        return Resolved("url", None, uri_or_path)
    p = Path(uri_or_path).expanduser()
    if not p.is_file():
        raise InfraError(stage, f"local input not found: {p}")
    return Resolved("path", p, None)


def download(url: str, out: Path, stage: str, max_bytes: int = DOWNLOAD_MAX_BYTES) -> Path:
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            n = 0
            with open(out, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    n += len(chunk)
                    if n > max_bytes:
                        raise InfraError(stage, f"download exceeds {max_bytes} bytes: {url[:120]}")
                    f.write(chunk)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise InfraError(stage, f"download network error: {e}")
    except requests.HTTPError as e:
        raise InfraError(stage, f"download failed: {e}")
    return out


# --------------------------------------------------------------------------
# ffmpeg / ffprobe wrappers (fail fast, plan §28)
# --------------------------------------------------------------------------


def ffprobe_duration(video_path: Path, ffprobe: str) -> float:
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise InfraError("ffprobe", r.stderr.strip()[:500])
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (ValueError, KeyError) as e:
        raise InfraError("ffprobe", f"cannot parse duration: {e}")


def ffmpeg_extract_frame(video_path: Path, ts: float, out: Path, ffmpeg: str) -> Path:
    r = subprocess.run(
        [ffmpeg, "-y", "-ss", str(ts), "-i", str(video_path),
         "-frames:v", "1", "-q:v", str(FFMPEG_QV), str(out)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not out.is_file():
        raise InfraError("ffmpeg_extract", r.stderr.strip()[:500])
    return out


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------


def b64_jpeg(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def data_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + b64_jpeg(path)


def image_block_url(url: str) -> dict:
    return {"type": "image", "source": {"type": "url", "url": url}}


def image_block_b64(path: Path) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": b64_jpeg(path)}}


def validate_jpeg_aspect(jpeg_bytes: bytes, stage: str) -> None:
    """Pillow decode + aspect sanity check ±5% of 3:4 (plan Q39)."""
    from PIL import Image
    import io

    try:
        img = Image.open(io.BytesIO(jpeg_bytes))
        img.verify()
        img = Image.open(io.BytesIO(jpeg_bytes))
        w, h = img.size
    except Exception as e:
        raise InfraError(stage, f"image does not decode via Pillow: {e}")
    expected = ASPECT_W / ASPECT_H
    if abs((w / h) - expected) / expected > ASPECT_TOLERANCE:
        raise InfraError(stage, f"aspect {w}x{h} outside ±5% of {ASPECT_RATIO}")


def download_as_jpeg_b64(url: str, stage: str, max_dim: int = 400) -> str:
    """Download a generated KF URL, validate aspect, downscale for preview,
    return base64 JPEG (plan §12/§39). The API rejects payloads > ~1 MB, so
    previews are downscaled to max_dim on the longest side."""
    from PIL import Image
    import io

    out = download(url, Path(tempfile.mkstemp(suffix=".jpg")[1]), stage)
    data = out.read_bytes()
    out.unlink(missing_ok=True)
    validate_jpeg_aspect(data, stage)
    # Downscale for preview to keep payload under ~500 KB
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((round(w * ratio), round(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------------
# Seedream i2i (plan §5.2/Q24: model/aspect/etc. driver-pinned, seeds round-trip)
# --------------------------------------------------------------------------


def seedream_i2i(raw_frame: Path, style: Resolved | None, prompt: str,
                 negative_prompt: str, seed: int, api_key: str) -> dict:
    """One Seedream i2i call → {url, seed, prompt_used, negative_prompt_used}.

    negative_prompt is composed into the prompt text (the images/generations
    surface has no stable negative-prompt field across Seedream versions);
    both are recorded separately in the result per plan §5.2.
    """
    full_prompt = prompt
    if negative_prompt:
        full_prompt = f"{prompt}\n\nStrictly avoid: {negative_prompt}"
    images = [data_uri(raw_frame)]
    if style is not None:
        images.append(style.url if style.kind == "url" else data_uri(style.path))
    body = {
        "model": SEEDREAM_MODEL_ID,
        "prompt": full_prompt,
        "image": images,
        "size": SEEDREAM_SIZE,
        "response_format": SEEDREAM_RESPONSE_FORMAT,
        "watermark": False,
        "seed": seed,
    }
    if SEEDREAM_GUIDANCE_SCALE is not None:
        body["guidance_scale"] = SEEDREAM_GUIDANCE_SCALE
    resp = _request("seedream_i2i", "POST", f"{INFERENCE_BASE_URL}/images/generations",
                    headers=_ark_headers(api_key), json=body, timeout=180)
    try:
        url = resp["data"][0]["url"]
    except (KeyError, IndexError, TypeError):
        raise InfraError("seedream_i2i", f"no image URL in response: {str(resp)[:400]}")
    # Sanity check: HTTP 200 + Pillow decode + ±5% of 3:4 (plan Q39).
    download_as_jpeg_b64(url, "seedream_i2i")
    return {"url": url, "seed": seed, "prompt_used": prompt,
            "negative_prompt_used": negative_prompt}


# --------------------------------------------------------------------------
# Agent bootstrap (plan §10 — reviser keys only, merged into shared cache)
# --------------------------------------------------------------------------


def _cache_path() -> Path:
    return Path(AGENTS_CACHE_PATH).expanduser()


def _load_cache() -> dict:
    p = _cache_path()
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except ValueError:
            return {}
    return {}


def _write_cache(cache: dict) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2) + "\n")


def _reviser_prompt_hash() -> str:
    return hashlib.sha256((REVISER_SYSTEM_PROMPT + VERSION).encode("utf-8")).hexdigest()


def upload_skill_zip(zip_path: str, api_key: str) -> str:
    """Upload a custom skill zip to the Managed Agents Skills API.

    POST /api/v3/skills with multipart/form-data; returns the skill ID
    (e.g. "skill-20260702082507-x6vpp").
    """
    url = f"{INFERENCE_BASE_URL}/skills"
    with open(zip_path, "rb") as f:
        resp = _request(
            "upload_skill", "POST", url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"files": (os.path.basename(zip_path), f, "application/zip")},
            timeout=120,
        )
    return resp["id"]


def _skill_zip_hash(zip_path: str) -> str:
    """SHA-256 of the zip file content, for cache-keying."""
    return hashlib.sha256(Path(zip_path).read_bytes()).hexdigest()


def _resolve_skill_ids(args) -> list[str]:
    """Resolve all skill IDs to attach to the Reviser agent.

    Combines:
    - Pre-existing skill IDs from --seedream-prompt-skill-id
    - Zip files from --skill-zip (uploaded if not yet cached)

    Skill zips are uploaded once and cached in agents.json by content hash.
    """
    skill_ids: list[str] = []

    # Pre-existing skill ID
    if args.seedream_prompt_skill_id:
        skill_ids.append(args.seedream_prompt_skill_id)

    # Skill zip files — upload if not cached
    zip_paths: list[str] = getattr(args, "skill_zip", None) or []
    if zip_paths:
        cache = _load_cache()
        skill_zip_cache: dict[str, str] = cache.get("skill_zip_cache", {})
        for zp in zip_paths:
            zhash = _skill_zip_hash(zp)
            if zhash in skill_zip_cache:
                skill_ids.append(skill_zip_cache[zhash])
            else:
                sid = upload_skill_zip(zp, args.ark_api_key)
                skill_zip_cache[zhash] = sid
                skill_ids.append(sid)
        cache["skill_zip_cache"] = skill_zip_cache
        _write_cache(cache)

    return skill_ids


def _reviser_skills(skill_ids: list[str]) -> list:
    return [{"type": "custom", "skill_id": sid} for sid in skill_ids]


def create_environment(args) -> str:
    """Minimal sandboxed Environment: no packages, no env vars, no vaults (Q19)."""
    # §17 open item resolved: config.networking is REQUIRED and the API only
    # accepts "unrestricted" (probed 2026-07-31: "none"/"restricted" → 400).
    # Acceptable here because the Reviser has no built-in tools, so the sandbox
    # never executes anything.
    try:
        resp = _request(
            "create_environment", "POST", f"{INFERENCE_BASE_URL}/environments",
            headers=_ark_headers(args.ark_api_key),
            json={
                "name": "kf-qc-reviser-env",
                "description": "Minimal sandbox for the kf-qc Reviser; custom tools execute driver-side.",
                "config": {"type": "cloud", "networking": {"type": "unrestricted"}},
            },
        )
        return resp["id"]
    except InfraError as e:
        # Names are unique per project (409 ResourceConflict) — reuse the
        # existing environment so bootstrap stays idempotent.
        if "ResourceConflict" not in e.error:
            raise
    listing = _request("list_environments", "GET", f"{INFERENCE_BASE_URL}/environments",
                       headers=_ark_headers(args.ark_api_key))
    items = listing.get("data") or listing.get("items") or []
    for env in items:
        if isinstance(env, dict) and env.get("name") == "kf-qc-reviser-env":
            return env["id"]
    raise InfraError("create_environment", "409 conflict but no existing env found by name")


def create_reviser_agent(args, skill_ids: list[str]) -> tuple[str, int]:
    resp = _request(
        "create_agent", "POST", f"{INFERENCE_BASE_URL}/agents",
        headers=_ark_headers(args.ark_api_key),
        json={
            "name": "kf-qc-reviser",
            "description": "QC-gate Reviser: regenerates failing styled keyframes via 3 custom tools.",
            "model": {"id": MODEL_ID, "speed": "standard"},
            "system": REVISER_SYSTEM_PROMPT,
            "tools": REVISER_TOOLS,  # 3 type:"custom" entries only — no built-in toolset (Q19)
            "skills": _reviser_skills(skill_ids),
        },
    )
    return resp["id"], resp.get("version", 1)


def update_reviser_agent(args, agent_id: str, skill_ids: list[str]) -> int:
    """Version-drift PUT (plan §10.5): push current prompt/tools/skills."""
    current = _request("get_agent", "GET", f"{INFERENCE_BASE_URL}/agents/{agent_id}",
                       headers=_ark_headers(args.ark_api_key))
    resp = _request(
        "update_agent", "POST", f"{INFERENCE_BASE_URL}/agents/{agent_id}",
        headers=_ark_headers(args.ark_api_key),
        json={
            "version": current["version"],
            "system": REVISER_SYSTEM_PROMPT,
            "tools": REVISER_TOOLS,
            "skills": _reviser_skills(skill_ids),
        },
    )
    return resp.get("version", current["version"] + 1)


def bootstrap_reviser(args) -> tuple[str, str]:
    """→ (reviser_agent_id, environment_id). Explicit IDs skip bootstrap (§10.4)."""
    if args.reviser_agent_id and args.environment_id:
        return args.reviser_agent_id, args.environment_id
    cache = _load_cache()
    prompt_hash = _reviser_prompt_hash()
    agent_id = args.reviser_agent_id or cache.get("reviser_agent_id")
    env_id = args.environment_id or cache.get("environment_id")

    # Resolve skill IDs (upload zips if needed, combine with pre-existing IDs)
    skill_ids = _resolve_skill_ids(args)

    if not env_id:
        env_id = create_environment(args)

    # Check if agent exists and prompt hash matches
    if agent_id and cache.get("reviser_prompt_hash") == prompt_hash:
        # Still need to update skills if they changed (skill zips may have been re-uploaded)
        cached_skill_ids = cache.get("reviser_skill_ids", [])
        if sorted(skill_ids) != sorted(cached_skill_ids):
            version = update_reviser_agent(args, agent_id, skill_ids)
            cache["reviser_version"] = version
        return agent_id, env_id  # cache hit

    if agent_id:  # version/prompt drift → PUT update (§10.5)
        version = update_reviser_agent(args, agent_id, skill_ids)
    else:
        agent_id, version = create_reviser_agent(args, skill_ids)
    cache.update({
        "reviser_agent_id": agent_id,
        "reviser_version": version,
        "environment_id": env_id,
        "reviser_prompt_hash": prompt_hash,
        "reviser_skill_ids": skill_ids,
        "version": VERSION,
    })
    _write_cache(cache)
    return agent_id, env_id


# --------------------------------------------------------------------------
# Session primitives (plan §11/§12/Q12/Q41)
# --------------------------------------------------------------------------


def create_session(args, agent_id: str, env_id: str) -> str:
    resp = _request("create_session", "POST", f"{INFERENCE_BASE_URL}/sessions",
                    headers=_ark_headers(args.ark_api_key),
                    json={"agent": agent_id, "environment_id": env_id})
    return resp["id"]


def get_session(args, session_id: str) -> dict:
    return _request("get_session", "GET", f"{INFERENCE_BASE_URL}/sessions/{session_id}",
                    headers=_ark_headers(args.ark_api_key))


def send_message(args, session_id: str, content_blocks: list) -> None:
    _request("send_message", "POST", f"{INFERENCE_BASE_URL}/sessions/{session_id}/events",
             headers=_ark_headers(args.ark_api_key),
             json={"events": [{"type": "user.message", "content": content_blocks}]})


def poll_events(args, session_id: str, after: str | None) -> list:
    url = f"{INFERENCE_BASE_URL}/sessions/{session_id}/events"
    if after:
        url += f"?after={after}"
    resp = _request("poll_events", "GET", url, headers=_ark_headers(args.ark_api_key),
                    timeout=30)
    if isinstance(resp, dict):
        return resp.get("data", resp.get("events", []))
    return resp if isinstance(resp, list) else []


def send_tool_result(args, session_id: str, tool_use_id: str, is_error: bool,
                     content: list) -> None:
    """Plan §12: wrapped in an "events" array; text + optional base64 image."""
    _request("send_tool_result", "POST",
             f"{INFERENCE_BASE_URL}/sessions/{session_id}/events",
             headers=_ark_headers(args.ark_api_key),
             json={"events": [{
                 "type": "user.custom_tool_result",
                 "custom_tool_use_id": tool_use_id,
                 "is_error": is_error,
                 "content": content,
             }]})


def tool_uses_from(events: list) -> list:
    """Tolerant extraction of custom-tool call requests (exact event type name
    is an open item — see header). Skips our own user.custom_tool_result events."""
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", ""))
        if "custom_tool" not in t or "result" in t:
            continue
        out.append({
            "id": e.get("custom_tool_use_id") or e.get("id"),
            "name": e.get("name") or e.get("tool_name"),
            "input": e.get("input") or e.get("arguments") or {},
        })
    return [u for u in out if u["id"] and u["name"]]


def count_turns(events: list) -> int:
    """LLM turns ≈ assistant message events + tool-use events (§22 cap of 8)."""
    n = 0
    for e in events:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", ""))
        if "custom_tool" in t and "result" not in t:
            n += 1
        elif ("message" in t or "text" in t) and not t.startswith("user"):
            n += 1
    return n


# --------------------------------------------------------------------------
# Evaluator rubric handling (plan §3/§4 — driver recomputes pass/fail)
# --------------------------------------------------------------------------


def recompute_kf_pass(scores: dict) -> tuple[bool, list[str]]:
    failed = []
    for dim, minimum in {**HARD_GATE_MIN, **SOFT_GATE_MIN}.items():
        entry = scores.get(dim) or {}
        score = entry.get("score", 0)
        if not isinstance(score, (int, float)) or score < minimum:
            failed.append(dim)
    return (not failed), failed


def parse_rubric(rubric: dict) -> tuple[set[str], set[str], dict]:
    """→ (failing_anchor_ids, approved_anchor_ids, per-anchor feedback).
    Anchors absent from the rubric carry over as approved (rounds ≥2, Q23)."""
    failing, approved, feedback = set(), set(), {}
    for kf in rubric.get("keyframes", []):
        anchor = kf.get("anchor_id")
        if anchor not in ANCHOR_IDS:
            continue
        ok, failed_dims = recompute_kf_pass(kf.get("scores", {}))
        if ok:
            approved.add(anchor)
        else:
            failing.add(anchor)
            feedback[anchor] = {
                "failed_dimensions": failed_dims,
                "scores": {d: kf.get("scores", {}).get(d) for d in failed_dims},
                "suggested_focus": kf.get("suggested_focus", ""),
            }
    for anchor in ANCHOR_IDS:
        if anchor not in failing and anchor not in approved:
            approved.add(anchor)
    return failing, approved, feedback


def load_keyframes(path: str) -> dict:
    """--current-keyframes: §44-shaped {"keyframes":[...]}, a bare list, or an
    anchor-keyed dict → normalized {anchor_id: entry}."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "keyframes" in data:
        data = data["keyframes"]
    if isinstance(data, list):
        entries = {e["anchor_id"]: e for e in data}
    elif isinstance(data, dict):
        entries = data
    else:
        raise InfraError("load_keyframes", "unrecognized keyframes JSON shape")
    for anchor in ANCHOR_IDS:
        if anchor not in entries:
            raise InfraError("load_keyframes", f"missing anchor {anchor}")
        e = entries[anchor]
        e.setdefault("image_url", e.get("keyframe_url"))
        if not e.get("image_url"):
            raise InfraError("load_keyframes", f"anchor {anchor} has no image_url")
    return entries


# --------------------------------------------------------------------------
# Reviser user message (plan §11.3)
# --------------------------------------------------------------------------


def build_reviser_message(style: Resolved | None, style_prompt: str | None,
                          raw_frames: dict, timestamps: dict,
                          keyframes: dict, failing: set, approved: set,
                          feedback: dict, coherence_notes: str,
                          history_text: str | None) -> list:
    blocks = []
    blocks.append({"type": "text", "text": "## Style reference (the art style the regenerated keyframes MUST match):"})
    if style is not None:
        blocks.append(image_block_url(style.url) if style.kind == "url"
                      else image_block_b64(style.path))
    else:
        blocks.append({"type": "text", "text": f"Style prompt (no style image provided): {style_prompt}"})

    blocks.append({"type": "text", "text": "## Raw video frames at current anchor timestamps (geometry/composition ground truth):"})
    for anchor in ANCHOR_IDS:
        blocks.append({"type": "text", "text": f"### Raw frame at t={timestamps[anchor]}s ({anchor}):"})
        blocks.append(image_block_b64(raw_frames[anchor]))

    if approved:
        blocks.append({"type": "text", "text": "## Approved styled keyframes (do NOT modify these — use as cross-anchor coherence reference):"})
        for anchor in ANCHOR_IDS:
            if anchor in approved:
                blocks.append({"type": "text", "text": f"### Styled keyframe ({anchor}) [approved]:"})
                blocks.append(image_block_url(keyframes[anchor]["image_url"]))

    blocks.append({"type": "text", "text": "## Failing styled keyframes from the previous Evaluator round (fix these):"})
    for anchor in ANCHOR_IDS:
        if anchor in failing:
            blocks.append({"type": "text", "text": f"### Styled keyframe ({anchor}) [needs fix]:"})
            blocks.append(image_block_url(keyframes[anchor]["image_url"]))

    fb = {"per_anchor": feedback}
    if coherence_notes:
        fb["cross_anchor_coherence_notes"] = coherence_notes
    blocks.append({"type": "text", "text": "## Evaluator feedback for failing anchors:\n"
                                           + json.dumps(fb, indent=2)})

    blocks.append({"type": "text", "text": "## Prior revisions attempted:\n"
                                           + (history_text or "none — this is the first revision round.")})

    blocks.append({"type": "text", "text": (
        "## Your task\n"
        f"Regenerate styled keyframes for these failing anchors ONLY: {sorted(failing)}. "
        f"Approved anchors (do NOT touch): {sorted(approved)}. "
        "For each failing anchor: study the Evaluator feedback and the raw frame, "
        "engineer a corrected Seedream prompt (use your attached prompt-engineering "
        "skill), and call generate_keyframe. Inspect each preview. When every "
        "failing anchor has a regenerated keyframe, call submit_revision exactly "
        "once with the full regenerated list. "
        f"Caps: {REVISER_MAX_TURNS} turns, {REVISER_MAX_KF_PER_ANCHOR} generations "
        f"per anchor, {REVISER_MAX_KF_TOTAL} generations total, "
        f"{REVISER_WALL_CLOCK_SEC}s wall clock — the session is force-closed at "
        "any cap, so be efficient."
    )})
    return blocks


# --------------------------------------------------------------------------
# Reviser round state + tool execution
# --------------------------------------------------------------------------


class ReviserState:
    def __init__(self, video_path: Path, duration: float, style, style_prompt,
                 keyframes, failing, approved, args, tmp):
        self.video_path = video_path
        self.duration = duration
        self.style = style
        self.style_prompt = style_prompt
        self.keyframes = {a: dict(e) for a, e in keyframes.items()}
        self.failing = failing
        self.approved = approved
        self.args = args
        self.tmp = tmp
        self.timestamps = {a: float(keyframes[a].get("timestamp_sec", 0)) for a in ANCHOR_IDS}
        self.seeds = {a: keyframes[a].get("seed") for a in ANCHOR_IDS}
        self.raw_frames: dict[str, Path] = {}
        self.kf_calls_per_anchor = {a: 0 for a in ANCHOR_IDS}
        self.kf_calls_total = 0
        self.generated_urls: dict[str, dict] = {}  # url → generation record
        self.actions: list[dict] = []  # audit log (§9 reviser_actions shape)


def execute_extract_keyframe(state: ReviserState, inp: dict) -> tuple[list, bool]:
    anchor, ts = inp["anchor_id"], float(inp["timestamp_sec"])
    if ts >= state.duration:
        return [{"type": "text", "text": json.dumps({
            "ok": False,
            "error": f"timestamp_sec {ts} >= video duration {state.duration:.2f}s",
        })}], True
    out = state.tmp / f"{anchor}_raw_{ts:.2f}.jpg"
    ffmpeg_extract_frame(state.video_path, ts, out, state.args.ffmpeg_path)
    state.raw_frames[anchor] = out
    state.timestamps[anchor] = ts
    state.actions.append({"tool": "extract_keyframe", "input": inp})
    return [
        {"type": "text", "text": json.dumps({"ok": True, "anchor_id": anchor,
                                             "timestamp_sec": ts})},
        image_block_b64(out),
    ], False


def execute_generate_keyframe(state: ReviserState, inp: dict) -> tuple[list, bool]:
    anchor = inp["anchor_id"]
    prompt = inp["prompt"]
    negative = inp.get("negative_prompt", "")
    force_new = bool(inp.get("force_new_seed", False))
    ts = state.timestamps[anchor]  # driver state is source of truth for the raw frame
    seed = state.seeds.get(anchor)
    if seed is None or force_new:
        seed = random.randint(0, 2**31 - 1)
    raw = state.raw_frames[anchor]
    result = seedream_i2i(raw, state.style, prompt, negative, seed,
                          state.args.ark_api_key)
    state.seeds[anchor] = result["seed"]
    state.kf_calls_per_anchor[anchor] += 1
    state.kf_calls_total += 1
    record = {"anchor_id": anchor, "timestamp_sec": ts,
              "keyframe_url": result["url"], "prompt_used": result["prompt_used"],
              "negative_prompt_used": result["negative_prompt_used"],
              "seed": result["seed"]}
    state.generated_urls[result["url"]] = record
    state.keyframes[anchor] = {**record, "image_url": result["url"]}
    state.actions.append({"tool": "generate_keyframe", "input": inp,
                          "result": {"keyframe_url": result["url"],
                                     "seed": result["seed"]}})
    text = {"ok": True, "anchor_id": anchor, "timestamp_sec": ts,
            "keyframe_url": result["url"], "seed": result["seed"],
            "prompt_used": result["prompt_used"],
            "negative_prompt_used": result["negative_prompt_used"],
            "model": SEEDREAM_MODEL_ID}
    preview_b64 = download_as_jpeg_b64(result["url"], "generate_keyframe")
    return [
        {"type": "text", "text": json.dumps(text)},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": preview_b64}},
    ], False


def validate_submit_revision(state: ReviserState, inp: dict) -> str | None:
    """Sanity guards (plan §5.3): regenerated ⊆ failing anchors; URLs must come
    from this session's generate_keyframe calls (no hallucinated URLs)."""
    for item in inp.get("regenerated", []):
        anchor = item.get("anchor_id")
        if anchor not in state.failing:
            return (f"anchor {anchor} was not a failing anchor in the previous "
                    f"Evaluator round — approved anchors must not be regenerated")
        if item.get("keyframe_url") not in state.generated_urls:
            return (f"keyframe_url for {anchor} was not produced by "
                    f"generate_keyframe in this session")
    covered = {i["anchor_id"] for i in inp.get("regenerated", [])}
    missing = state.failing - covered
    if missing:
        return f"failing anchors not regenerated: {sorted(missing)}"
    return None


def forced_delta(state: ReviserState, reason: str, turns: int) -> dict:
    """Auto-built delta on cap hit / idle-without-submit (Q22, Q41)."""
    regenerated = []
    for anchor in sorted(state.failing):
        latest = None
        for rec in state.generated_urls.values():
            if rec["anchor_id"] == anchor:
                latest = rec
        if latest:
            regenerated.append({**latest, "changes_made": "driver-forced submit"})
    return {
        "regenerated": regenerated,
        "unchanged_approved_anchor_ids": sorted(state.approved),
        "attempts_used": max(1, state.kf_calls_total),
        "notes": f"driver-forced submit: {reason} (turns={turns}, "
                 f"kf_calls={state.kf_calls_total})",
        "driver_forced": True,
    }


# --------------------------------------------------------------------------
# Reviser round driver
# --------------------------------------------------------------------------


def run_reviser_round(args) -> dict:
    with tempfile.TemporaryDirectory(prefix="kf-qc-reviser-") as tmpdir:
        tmp = Path(tmpdir)

        # --- resolve inputs -------------------------------------------------
        raw_video = resolve_input(args.raw_video, args, tmp, "resolve_raw_video")
        video_path = (raw_video.path if raw_video.kind == "path"
                      else download(raw_video.url, tmp / "raw_video.mp4", "download_raw_video"))
        style = (resolve_input(args.style_image, args, tmp, "resolve_style_image")
                 if args.style_image else None)
        if style is None and not args.style_prompt:
            raise InfraError("args", "at least one of --style-image/--style-prompt is required")

        rubric = json.loads(Path(args.graded_rubric).read_text())
        keyframes = load_keyframes(args.current_keyframes)
        failing, approved, feedback = parse_rubric(rubric)
        coherence_notes = rubric.get("cross_anchor_coherence_notes", "")
        history_text = (Path(args.history_json).read_text() if args.history_json else None)

        duration = ffprobe_duration(video_path, args.ffprobe_path)
        state = ReviserState(video_path, duration, style, args.style_prompt,
                             keyframes, failing, approved, args, tmp)

        if not failing:
            return {"status": "no_revision_needed", "round": rubric.get("round"),
                    "delta": None,
                    "merged_keyframes": [_merged_entry(state, a) for a in ANCHOR_IDS],
                    "reviser_actions": [], "kf_generations_used": 0}

        # Raw frames at current anchor timestamps (§11.3 block 2).
        for anchor in ANCHOR_IDS:
            state.raw_frames[anchor] = ffmpeg_extract_frame(
                video_path, state.timestamps[anchor],
                tmp / f"{anchor}_raw.jpg", args.ffmpeg_path)

        # --- bootstrap + session -------------------------------------------
        agent_id, env_id = bootstrap_reviser(args)
        session_id = create_session(args, agent_id, env_id)
        blocks = build_reviser_message(style, args.style_prompt, state.raw_frames,
                                       state.timestamps, keyframes, failing,
                                       approved, feedback, coherence_notes,
                                       history_text)
        send_message(args, session_id, blocks)

        # --- poll / tool-dispatch loop (Q12, Q41, §22 caps) -----------------
        # The events API always returns the full history (no server-side cursor
        # filtering — the clock icon in the docs shows reconnection semantics).
        # Client-side dedup: track seen event IDs, process only new events.
        deadline = time.time() + REVISER_WALL_CLOCK_SEC
        seen_ids, all_events, delta = set(), [], None
        force_reason = None
        try:
            while time.time() < deadline:
                time.sleep(POLL_INTERVAL_SEC)
                events = poll_events(args, session_id, None)  # full history each time
                # Filter to events not yet seen
                new_events = [e for e in events
                              if e.get("id") and e["id"] not in seen_ids]
                for e in new_events:
                    if e.get("id"):
                        seen_ids.add(e["id"])
                all_events.extend(new_events)

                if count_turns(all_events) >= REVISER_MAX_TURNS:
                    force_reason = f"turn cap {REVISER_MAX_TURNS} reached"
                    break

                submitted = False
                for use in tool_uses_from(new_events):
                    name, inp = use["name"], use["input"]
                    if name == "extract_keyframe":
                        content, is_err = execute_extract_keyframe(state, inp)
                        send_tool_result(args, session_id, use["id"], is_err, content)
                        time.sleep(POST_TOOL_WAIT_SEC)
                    elif name == "generate_keyframe":
                        anchor = inp.get("anchor_id", "")
                        if (state.kf_calls_per_anchor.get(anchor, 0) >= REVISER_MAX_KF_PER_ANCHOR
                                or state.kf_calls_total >= REVISER_MAX_KF_TOTAL):
                            force_reason = f"generate_keyframe cap hit (anchor={anchor})"
                            break
                        content, is_err = execute_generate_keyframe(state, inp)
                        send_tool_result(args, session_id, use["id"], is_err, content)
                        time.sleep(POST_TOOL_WAIT_SEC)
                    elif name == "submit_revision":
                        err = validate_submit_revision(state, inp)
                        if err:
                            send_tool_result(args, session_id, use["id"], True,
                                             [{"type": "text", "text": json.dumps(
                                                 {"ok": False, "error": err})}])
                            time.sleep(POST_TOOL_WAIT_SEC)
                        else:
                            delta = {**inp, "driver_forced": False}
                            state.actions.append({"tool": "submit_revision",
                                                  "input": inp, "driver_forced": False})
                            submitted = True
                            break  # §12: no tool result — ends the session
                if submitted or force_reason:
                    break

                status = get_session(args, session_id).get("status")
                if status == "terminated":
                    raise InfraError("reviser_session", "session terminated")
                # Check for pending tool calls in the latest events
                pending_tool = any(
                    e.get("type") == "session.status_idle"
                    and isinstance(e.get("stop_reason"), dict)
                    and e.get("stop_reason", {}).get("type") == "requires_action"
                    for e in new_events
                )
                if not pending_tool and status == "idle" and seen_ids:
                    force_reason = "session idle without submit_revision"
                    break
        finally:
            if delta is None and force_reason:
                state.actions.append({"tool": "submit_revision", "input": None,
                                      "driver_forced": True})
        if delta is None:
            if force_reason is None:
                force_reason = f"wall clock {REVISER_WALL_CLOCK_SEC}s exceeded"
            delta = forced_delta(state, force_reason, count_turns(all_events))

        merged = []
        regen_by_anchor = {r["anchor_id"]: r for r in delta["regenerated"]}
        for anchor in ANCHOR_IDS:
            if anchor in regen_by_anchor:
                r = regen_by_anchor[anchor]
                rec = state.generated_urls.get(r["keyframe_url"], {})
                merged.append({"anchor_id": anchor, "timestamp_sec": r["timestamp_sec"],
                               "image_url": r["keyframe_url"],
                               "prompt_used": r["prompt_used"],
                               "negative_prompt_used": rec.get("negative_prompt_used", ""),
                               "seed": r["seed"]})
            else:
                merged.append(_merged_entry(state, anchor))

        return {
            "status": "driver_forced" if delta.get("driver_forced") else "submitted",
            "round": rubric.get("round"),
            "reviser_session_id": session_id,
            "reviser_agent_id": agent_id,
            "delta": delta,
            "merged_keyframes": merged,
            "reviser_actions": state.actions,
            "kf_generations_used": state.kf_calls_total,
        }


def _merged_entry(state: ReviserState, anchor: str) -> dict:
    e = state.keyframes[anchor]
    return {"anchor_id": anchor, "timestamp_sec": e.get("timestamp_sec", 0),
            "image_url": e["image_url"], "prompt_used": e.get("prompt_used", ""),
            "negative_prompt_used": e.get("negative_prompt_used", ""),
            "seed": e.get("seed")}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Keyframe QC Reviser — one revision round of the QC gate "
                    "(.pi/keyframe_gate_reviser scope; plan §5/§11.3/§21/§22).")
    p.add_argument("--graded-rubric", required=True,
                   help="Evaluator graded rubric JSON (plan §4)")
    p.add_argument("--current-keyframes", required=True,
                   help="Current KF set JSON: §44-shaped, bare list, or anchor-keyed dict")
    p.add_argument("--raw-video", required=True,
                   help="local path / https:// URL / asset:// URI (extract_keyframe source)")
    p.add_argument("--style-image", help="local path / https:// URL / asset:// URI")
    p.add_argument("--style-prompt", help="used only if no --style-image (Q17)")
    p.add_argument("--history-json", help="prior-rounds summary text/JSON (Q37)")
    p.add_argument("--output-revision-json", required=True,
                   help="pretty-printed revision output (delta + merged keyframes)")
    p.add_argument("--ark-api-key", default=os.environ.get("ARK_API_KEY"))
    p.add_argument("--ark-access-key", default=os.environ.get("ARK_ACCESS_KEY"))
    p.add_argument("--ark-secret-key", default=os.environ.get("ARK_SECRET_KEY"))
    p.add_argument("--reviser-agent-id", help="override cached/bootstrapped agent (§10.4)")
    p.add_argument("--environment-id", help="override cached/bootstrapped environment")
    p.add_argument("--seedream-prompt-skill-id",
                   help="ModelArk custom Skill ID attached to the Reviser (Q33)")
    p.add_argument("--skill-zip", action="append", default=[],
                   help="Path to a custom Skill zip file to upload and attach to the "
                        "Reviser agent. Can be specified multiple times.")
    p.add_argument("--ffmpeg-path", default="ffmpeg")
    p.add_argument("--ffprobe-path", default="ffprobe")
    args = p.parse_args(argv)
    if not args.ark_api_key:
        p.error("--ark-api-key or $ARK_API_KEY is required")
    if not args.style_image and not args.style_prompt:
        p.error("at least one of --style-image/--style-prompt is required")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    started = time.time()
    try:
        result = run_reviser_round(args)
    except InfraError as e:
        report = {"status": "infrastructure_error",
                  "infrastructure_error": {"stage": e.stage, "error": e.error},
                  "duration_seconds": round(time.time() - started, 1)}
        Path(args.output_revision_json).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, separators=(",", ":")))
        return 3
    result["duration_seconds"] = round(time.time() - started, 1)
    Path(args.output_revision_json).write_text(json.dumps(result, indent=2) + "\n")
    compact = {k: result[k] for k in ("status", "round", "kf_generations_used",
                                      "duration_seconds")}
    compact["reviser_session_id"] = result.get("reviser_session_id")
    print(json.dumps(compact, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
