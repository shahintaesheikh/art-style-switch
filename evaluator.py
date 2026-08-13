#!/usr/bin/env python3
"""Keyframe QC Gate — EVALUATOR component only (keyframe_gate_plan.md v0.1.0).

A BytePlus Managed Agent (doubao-seed-2-1-pro-260628, standard speed) that scores
styled keyframes against the plan §3 rubric (5 dimensions: geometry, composition,
medium, palette, line_quality) and returns strict JSON per the plan §4 schema.

Evaluator constraints (plan §1, Q10, Q19):
  * ZERO custom tools, ZERO built-in tools, NO skills, NO MCP, NO multiagent.
  * Fresh session per round (Q14/Q15); all KFs in a single prompt.
  * Round 1 scores all anchors; rounds >= 2 score only [NEW] anchors, with
    approved anchors shown for cross-anchor coherence but NOT rescored (Q23).
  * Driver recomputes pass/fail from thresholds; the model's `pass` boolean is
    advisory/debug only (Q20). One schema-retry nudge on parse failure (Q20).

This file is the evaluator half of qc_gate.py (plan §13). The Reviser, round-0
bootstrap, and outer 3-attempt loop live in their own worktree/file; the
functions here are written to be lifted into the merged driver unchanged.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time

import requests

try:  # corporate TLS interception — same pattern as pipeline/__init__.py
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - truststore is in requirements.txt
    pass

# ---------------------------------------------------------------------------
# Constants (plan §8 — evaluator-relevant subset; UPPER_SNAKE_CASE per Q47)
# ---------------------------------------------------------------------------

VERSION = "0.1.0"
MODEL_ID = "dola-seed-2-1-turbo-260628"  # Q10/Q36 substituted: doubao-seed-2-1-pro-260628
# is not enabled on this account; dola-seed-2-1-turbo is the accessible
# vision-capable equivalent (same pattern as pipeline/seedream.py's note).
MODEL_SPEED = "standard"
INFERENCE_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"  # Q35
AGENTS_CACHE_PATH = "~/.config/kf-qc/agents.json"  # §10
POLL_INTERVAL_SEC = 2   # Q12/Q41
POLL_TIMEOUT_SEC = 600  # Q41 (10-minute wall clock per session — v7 prompt needs more time)
INFRA_RETRY_WAIT_SEC = 5  # Q28
INFRA_MAX_RETRIES = 1     # Q28 (retry once)

ANCHOR_IDS = ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7")  # Q42 (8 anchors: 0%, ~14.3%, ~28.6%, ~42.9%, ~57.1%, ~71.4%, ~85.7%, 100%)

# Rubric thresholds (plan §3). Hard gates must be exactly 5; soft gates >= 4.
# medium and palette are now hard gates — style must match the reference exactly.
HARD_GATE_MIN = {"geometry": 5, "composition": 5, "medium": 5, "line_quality": 5}
SOFT_GATE_MIN = {"palette": 4}
ALL_DIMENSIONS = ("medium", "palette", "line_quality", "geometry", "composition")

# Minimal sandboxed Environment (Q19). Smoke test resolved the plan §17 open
# question: `config.networking` is a REQUIRED field, so it is set explicitly.
# The Evaluator has zero tools, so sandbox networking is inert; kept as
# "unrestricted" (the documented type) in case the runtime fetches user.message
# image URLs from inside the sandbox rather than server-side.
ENVIRONMENT_CONFIG = {"type": "cloud", "networking": {"type": "unrestricted"}}

# ---------------------------------------------------------------------------
# Evaluator system prompt (Q45 — module-level constant, versioned with driver)
# ---------------------------------------------------------------------------

EVALUATOR_SYSTEM_PROMPT = """You are the Keyframe QC Evaluator in a style-transfer video generation pipeline. Styled keyframes (produced by an image-to-image model from raw video frames) will drive a video generation model; your scores are the quality gate that decides whether those keyframes are good enough to use. You NEVER generate, edit, or repair images. You only inspect and score.

## Inputs you will receive (in this order)
1. A style reference — an image (the sole authority for the target art style)
2. Raw video frames, one per anchor, labeled with anchor ID and timestamp. These are the GEOMETRY and COMPOSITION ground truth.
3. Styled keyframes, one per anchor, each tagged either `[NEW in this round — score all 5 dimensions]` or `[previously approved — shown only for cross-anchor coherence, do NOT rescore]`.
4. (Rounds 2+) A compact summary of prior rounds.
5. The rubric, thresholds, and output schema.

## Rubric — 5 dimensions, each scored 1–5
- geometry: object count, positions, orientations, and vanishing point in the styled keyframe match its raw frame 1:1. Any added/removed/moved/rotated/resized/morphed object is a defect.
- composition: same framing, zoom, and crop as the raw frame; 3:4 aspect; no letterboxing.
- medium: rendering medium matches the target style EXACTLY (e.g. 3D, pencil + colored-pencil, anime cel, oil, photorealism, watercolor). ANY drift to a different medium is a defect — even if the result looks attractive, if the medium is not identical to the reference it must score ≤4. The medium must be perceptually indistinguishable. Cross-anchor medium CONSISTENCY is paramount — the styled keyframes must share one uniform medium across all anchors. A set of keyframes with a consistent medium (even slightly off from the reference) is BETTER than a set where some anchors match exactly and others drift. Penalize any anchor whose medium deviates from the other anchors' medium.
  Also assess the DEGREE of medium application: stroke weight, pencil pressure, hatching density, how heavily or sparsely the medium is laid down. Two keyframes may both use "colored pencil + graphite" but one applies it with heavy dense strokes while the other uses light sketchy strokes — this is a medium defect. The texture coarseness, stroke weight, and application intensity must be perceptually identical across all anchors and match the reference.
- palette: color treatment matches the target style EXACTLY (e.g. hatched crimson vs flat red, graphite-on-cream vs saturated color, colorful vs completely monochromatic).
- line_quality: mark/edge character matches the target style EXACTLY (e.g. sketchy graphite for pencil, clean cel lines for anime, painterly edges for oil). ANY deviation in linework character is a defect — the stroke type, edge quality, and mark-making must be perceptually indistinguishable from the reference.
  Also assess the FINENESS and PRECISION of linework: stroke width, detail richness, how tight or loose the linework is. Coarser, less detailed, or looser lines than the reference indicate a line quality defect — even if the stroke type (graphite) matches. A keyframe with noticeably fewer fine details, thicker lines, or less precise edges than the reference or sibling anchors should score lower on line_quality.

### Score anchors (apply to every dimension)
- 5 = matches the reference exactly on this dimension; no detectable deviation.
- 4 = minor deviation visible only on close inspection; still clearly on-target. NOTE: 4 is a FAILING score for geometry and composition (hard gates) — reserve 4 there for real, if small, structural/compositional defects.
- 3 = clear deviation or drift; the dimension is recognizably off-target.
- 2 = largely wrong; only traces of the target remain.
- 1 = completely wrong on this dimension.

### Hard gate note
Medium and line_quality are also hard gates (minimum 5). A score of 4 on medium or line_quality means the style does not match the reference exactly and counts as a failure — even if the deviation is subtle.

## Scoring rules
- MEDIUM/ART STYLE CONSISTENCY ACROSS ANCHORS IS A HARD REQUIREMENT. The final video interpolates style between these anchors, so any medium switching between anchors produces visible style jumps in the output. A keyframe that uses a different medium than its sibling anchors is a defect on its own medium score even if it individually matches the reference better. Uniformly "pretty close but consistent" beats "sometimes exact, sometimes off" with the video flickering between styles. Flag any cross-anchor medium drift in `cross_anchor_coherence_notes` and factor it into the affected anchor's medium score.
- DEGREE OF APPLICATION is scored within medium and line_quality. Evaluate not just the TYPE of medium or linework (e.g. "colored pencil + graphite") but the INTENSITY and DEGREE of its application. Compare stroke weight, pencil pressure, hatching density, texture roughness, line precision, shading tightness, and detail level against both the style reference AND sibling anchors. A keyframe with the correct medium type but applied at a markedly different intensity (e.g., much heavier strokes, much looser shading, much coarser lines) should score lower on both medium and line_quality. Cite specific differences in the degree of application in your rationales.
- Compare geometry and composition ONLY against the raw frame for the same anchor. Compare medium, palette, and line_quality ONLY against the style reference.
- Score every keyframe tagged `[NEW ...]` on all 5 dimensions. NEVER rescore keyframes tagged `[previously approved ...]` and never include them in the `keyframes` array — use them only as visual context for cross-anchor coherence.
- Be strict and evidence-based. Rationales must cite concrete visual evidence (e.g. "the left car is rotated ~15° vs the raw frame", "palette is saturated digital color, not graphite-on-cream"), not vibes.
- `suggested_focus` is a concrete, actionable fix hint for the downstream prompt-engineering Reviser (what to change in the generation prompt/seed/timestamp), only for failing keyframes; use an empty string for passing ones.
- Every round, also write `cross_anchor_coherence_notes`: whether the scored keyframes are stylistically consistent with each other and with any approved keyframes shown (medium, palette, line_quality drift ACROSS anchors), even when everything passes. In the coherence notes, explicitly call out any drift in the DEGREE of medium application across anchors (e.g. "A0 has finer, denser pencil strokes while A1 has looser, coarser strokes") — not just whether the medium type is the same.
- SUBJECT CONSISTENCY — the RED PLAYER CAR is the hero subject and must be consistent across anchors. In each keyframe, verify the red car's SHAPE, SILHOUETTE, and SHADING are the same car as in the raw frame and in the sibling anchors: same body proportions, same roof line, same wheel placement, same windows. A keyframe where the car's roof blends into the road surface, where the car body merges with the background, or where the car's shape/silhouette shifts is a defect. Score the car's subject consistency into the affected anchor's geometry and medium scores, and call out any car-specific drift in `cross_anchor_coherence_notes`.
- Echo each scored keyframe's `image_url` back exactly as provided.

## Output
Return exactly one JSON object matching the schema given in the user message. No prose, no markdown fences, no commentary outside the JSON."""

# Evaluator output schema (plan §4) — embedded in the user message and used
# for driver-side validation.
EVALUATOR_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["pass", "round", "keyframes", "cross_anchor_coherence_notes", "summary"],
    "properties": {
        "pass": {"type": "boolean"},
        "round": {"type": "integer"},
        "keyframes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["anchor_id", "timestamp_sec", "image_url", "scores",
                             "pass", "failed_dimensions", "suggested_focus"],
                "properties": {
                    "anchor_id": {"type": "string", "enum": list(ANCHOR_IDS)},
                    "timestamp_sec": {"type": "number"},
                    "image_url": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "required": list(ALL_DIMENSIONS),
                        "properties": {
                            d: {
                                "type": "object",
                                "required": ["score", "rationale"],
                                "properties": {
                                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                                    "rationale": {"type": "string"},
                                },
                            }
                            for d in ALL_DIMENSIONS
                        },
                    },
                    "pass": {"type": "boolean"},
                    "failed_dimensions": {"type": "array", "items": {"type": "string"}},
                    "suggested_focus": {"type": "string"},
                },
            },
        },
        "cross_anchor_coherence_notes": {"type": "string"},
        "summary": {"type": "string"},
    },
}

SCHEMA_RETRY_NUDGE = (  # Q20
    "Your previous response wasn't valid JSON following the schema. "
    "Return exactly one JSON object matching the schema — no prose, no "
    "markdown fences, no commentary. Required top-level keys: pass (bool), "
    "round (int), keyframes (array), cross_anchor_coherence_notes (string), "
    "summary (string). Each keyframes entry needs anchor_id, timestamp_sec, "
    "image_url, scores (all 5 dimensions, each {score: int 1-5, rationale: "
    "string}), pass (bool), failed_dimensions (array), suggested_focus (string)."
)


class EvaluatorInfraError(Exception):
    """Infrastructure failure (Q28): carries `stage` for the QC report."""

    def __init__(self, stage: str, error: str):
        super().__init__(f"{stage}: {error}")
        self.stage = stage
        self.error = error


# ---------------------------------------------------------------------------
# HTTP helpers (Bearer auth, Q28 retry policy)
# ---------------------------------------------------------------------------

class ArkClient:
    """Thin REST client for the Managed Agents API (plan §6 session primitives)."""

    def __init__(self, api_key: str, base_url: str = INFERENCE_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def request(self, method: str, path: str, stage: str, **kwargs) -> dict:
        """Q28: retry once after 5s on 429/5xx/network errors; 4xx fails fast."""
        url = f"{self.base_url}{path}"
        last_err: str | None = None
        for attempt in range(1 + INFRA_MAX_RETRIES):
            try:
                r = self.session.request(method, url, timeout=60, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = f"network error: {e}"
                if attempt < INFRA_MAX_RETRIES:
                    time.sleep(INFRA_RETRY_WAIT_SEC)
                    continue
                raise EvaluatorInfraError(stage, last_err) from e
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}: {r.text[:500]}"
                if attempt < INFRA_MAX_RETRIES:
                    time.sleep(INFRA_RETRY_WAIT_SEC)
                    continue
                raise EvaluatorInfraError(stage, last_err)
            if r.status_code >= 400:  # other 4xx — fail fast, no retry
                raise EvaluatorInfraError(stage, f"HTTP {r.status_code}: {r.text[:500]}")
            return r.json() if r.content else {}
        raise EvaluatorInfraError(stage, last_err or "unknown error")  # unreachable


# ---------------------------------------------------------------------------
# Agent bootstrap (plan §10 — evaluator-only slice)
# ---------------------------------------------------------------------------

def _prompt_hash() -> str:
    return hashlib.sha256((EVALUATOR_SYSTEM_PROMPT + VERSION).encode()).hexdigest()


def bootstrap_evaluator(client: ArkClient,
                        agent_id_override: str | None = None,
                        environment_id_override: str | None = None
                        ) -> tuple[str, int, str]:
    """Create/reuse the Evaluator agent + minimal Environment (plan §10).

    Returns (evaluator_agent_id, evaluator_agent_version, environment_id).
    Explicit overrides skip bootstrap entirely (§10.4). Cache is merged
    non-destructively so the Reviser half can share ~/.config/kf-qc/agents.json.
    """
    #Already a persisting agent session, reuse that
    if agent_id_override and environment_id_override:
        agent = client.request("GET", f"/agents/{agent_id_override}", stage="bootstrap")
        return agent_id_override, int(agent.get("version", 1)), environment_id_override

    cache_path = os.path.expanduser(AGENTS_CACHE_PATH)
    cache: dict = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}

    have_hash = cache.get("evaluator_prompt_hash") == _prompt_hash()
    if have_hash and cache.get("evaluator_agent_id") and cache.get("environment_id"):
        return (cache["evaluator_agent_id"],
                int(cache.get("evaluator_version", 1)),
                cache["environment_id"])

    # (a) environment — idempotent: on name conflict, reuse the existing one
    env_id = cache.get("environment_id")
    if not env_id:
        try:
            env = client.request("POST", "/environments", stage="bootstrap",
                                 json={"name": "kf-qc-gate-env",
                                       "description": "Minimal sandbox for the keyframe QC gate (no tools).",
                                       "config": ENVIRONMENT_CONFIG})
            env_id = env["id"]
        except EvaluatorInfraError as e:
            if "ResourceConflict" not in e.error:
                raise
            envs = client.request("GET", "/environments", stage="bootstrap")
            env_id = next(e2["id"] for e2 in envs.get("data", [])
                          if e2.get("name") == "kf-qc-gate-env")

    # (b/c) evaluator agent — NO tools/skills/mcp/multiagent keys at all (Q19)
    agent_id = cache.get("evaluator_agent_id")
    if agent_id:
        # version drift (§10.5): PUT updated prompt with current version
        current = client.request("GET", f"/agents/{agent_id}", stage="bootstrap")
        updated = client.request("POST", f"/agents/{agent_id}", stage="bootstrap",
                                  json={"version": int(current["version"]),
                                        "system": EVALUATOR_SYSTEM_PROMPT})
        agent_version = int(updated.get("version", int(current["version"]) + 1))
    else:
        try:
            agent = client.request("POST", "/agents", stage="bootstrap", json={
                "name": "kf-qc-evaluator",
                "description": "Keyframe QC gate Evaluator — scores styled keyframes "
                               "on the 5-dimension rubric; no tools.",
                "model": {"id": MODEL_ID, "speed": MODEL_SPEED},
                "system": EVALUATOR_SYSTEM_PROMPT,
            })
            agent_id = agent["id"]
            agent_version = int(agent.get("version", 1))
        except EvaluatorInfraError as e:
            if "ResourceConflict" not in e.error:
                raise
            agents = client.request("GET", "/agents", stage="bootstrap")
            found = next(a for a in agents.get("data", [])
                         if a.get("name") == "kf-qc-evaluator")
            agent_id = found["id"]
            agent_version = int(found.get("version", 1))

    cache.update({
        "evaluator_agent_id": agent_id,
        "evaluator_version": agent_version,
        "environment_id": env_id,
        "evaluator_prompt_hash": _prompt_hash(),
        "version": VERSION,
    })
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)
    return agent_id, agent_version, env_id


# ---------------------------------------------------------------------------
# Session primitives (plan §13)
# ---------------------------------------------------------------------------

def create_session(client: ArkClient, agent_id: str, env_id: str) -> str:
    s = client.request("POST", "/sessions", stage="create_session",
                       json={"agent": agent_id, "environment_id": env_id})
    return s["id"]


def send_message(client: ArkClient, session_id: str, content_blocks: list[dict]) -> None:
    # Smoke test: the API requires the plan §12 envelope ({"events": [...]}),
    # not the bare event object shown in the Managed Agents reference doc.
    client.request("POST", f"/sessions/{session_id}/events", stage="send_message",
                   json={"events": [{"type": "user.message", "content": content_blocks}]})


def poll_until_idle(client: ArkClient, session_id: str,
                    deadline_epoch: float | None = None) -> list[dict]:
    """2s cursor-based polling on /sessions/{id}/events?after=<id> (Q12/Q41).

    Stops when the session returns to `idle` (work complete), and raises on
    `terminated` (infra error) or timeout.
    """
    deadline = deadline_epoch or (time.time() + POLL_TIMEOUT_SEC)
    events: list[dict] = []
    seen_ids: set[str] = set()
    last_event_id: str | None = None

    #while deadline has not been hit yet
    while time.time() < deadline:
        path = f"/sessions/{session_id}/events"
        if last_event_id:
            path += f"?after={last_event_id}"
        batch = client.request("GET", path, stage="poll_events")
        # smoke test: events live under "data"; agent text arrives as
        # "agent.message" events with text content blocks. The `after` cursor
        # is ignored by the current API (full list returned every poll), so we
        # dedupe by event id.
        new_events = batch.get("data", batch if isinstance(batch, list) else [])
        for evt in new_events:
            if evt.get("id") and evt["id"] in seen_ids:
                continue
            events.append(evt)
            if evt.get("id"):
                seen_ids.add(evt["id"])
                last_event_id = evt["id"]
        status = client.request("GET", f"/sessions/{session_id}",
                                stage="poll_status").get("status")
        if status == "idle":
            # smoke test: a failed model request still ends in idle, with a
            # session.error event — surface it as an infra error, not as an
            # empty/missing response
            #check for error after idle status
            for evt in events:
                if evt.get("type") == "session.error":
                    err = evt.get("error", {})
                    raise EvaluatorInfraError(
                        "model_request",
                        f"{err.get('type', 'session.error')}: {err.get('message', '')[:500]}")
            return events
        if status == "terminated":
            raise EvaluatorInfraError("poll_status", f"session {session_id} terminated")
        time.sleep(POLL_INTERVAL_SEC)
    raise EvaluatorInfraError("poll_status",
                              f"session {session_id} did not reach idle within "
                              f"{POLL_TIMEOUT_SEC}s")


def extract_assistant_text(events: list[dict]) -> str:
    """Pull the agent's final text output out of polled session events.

    Event type names are confirmed at smoke test (plan §17); this walks any
    event carrying text content blocks and returns the LAST agent-originated
    text, which for a zero-tool agent is its single JSON answer.
    """
    texts: list[str] = []
    for evt in events:
        etype = str(evt.get("type", ""))
        if etype.startswith("user."):  # skip our own echoed messages
            continue
        content = evt.get("content") or evt.get("message", {}).get("content") or []
        if isinstance(content, str):
            texts.append(content)
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
    if not texts:
        raise EvaluatorInfraError("parse_response", "no agent text found in session events")
    return texts[-1]


# ---------------------------------------------------------------------------
# Per-round user-message layout (plan §11.1 / §11.2, visual order per Q38)
# ---------------------------------------------------------------------------

def _image_block(url_or_path: str) -> dict:
    """Image block for user.message (Q9/Q39): base64 JPEG inline (Q18).

    Smoke test showed that TOS presigned URLs from Seedream die within minutes
    (403), so the URL-passthrough path is unreliable. Every image is downloaded
    (or read from disk) and sent as base64 — the plan's fallback transport.
    """
    if url_or_path.startswith("data:"):
        # data:image/jpeg;base64,<...> -> base64 source block
        header, data = url_or_path.split(",", 1)
        media_type = header[5:].split(";")[0] or "image/jpeg"
        return {"type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data}}
    if url_or_path.startswith(("http://", "https://")):
        # download and base64-encode (URL-passthrough unreliable per smoke test)
        r = requests.get(url_or_path, timeout=60)
        r.raise_for_status()
        data = base64.b64encode(r.content).decode("ascii")
        return {"type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
    with open(url_or_path, "rb") as f:  # local path
        data = base64.b64encode(f.read()).decode("ascii")
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def build_evaluator_message(round_idx: int,
                            anchors: list[dict],
                            style_image_url: str | None,
                            style_prompt: str | None,
                            history_summary: str | None) -> list[dict]:
    """Content blocks per plan §11.1 (round 1) and §11.2 (rounds >= 2).

    `anchors` entries: {anchor_id, timestamp_sec, raw_frame_url, keyframe_url,
    status} where status is "new" (score it) or "approved" (coherence context
    only, do NOT rescore — Q23).
    """
    blocks: list[dict] = []

    # 1-2. style reference first (Q38); image is sole authority when present (Q17)
    blocks.append({"type": "text", "text":
                   "## Style reference (the art style the final keyframes MUST match):"})
    if style_image_url:
        blocks.append(_image_block(style_image_url))
    else:
        blocks.append({"type": "text", "text":
                       f"(No style image provided; target style description:)\n{style_prompt}"})

    # 3-4. raw frames = geometry/composition ground truth
    blocks.append({"type": "text", "text":
                   "## Raw video frames at anchor timestamps (geometry/composition ground "
                   "truth — object count, positions, orientations, framing, and vanishing "
                   "point must match these exactly):"})
    for a in anchors:
        blocks.append({"type": "text", "text":
                       f"### Raw frame at t={a['timestamp_sec']}s ({a['anchor_id']}):"})
        blocks.append(_image_block(a["raw_frame_url"]))

    # 5-6. styled keyframes, tagged [NEW] vs [previously approved] (Q23)
    blocks.append({"type": "text", "text":
                   "## Styled keyframes to score (apply the rubric below to each; compare "
                   "geometry/composition against the raw frames above; compare "
                   "medium/palette/line_quality against the style reference above):"})
    for a in anchors:
        tag = ("[NEW in this round — score all 5 dimensions]" if a["status"] == "new"
               else "[previously approved — shown only for cross-anchor coherence, "
                    "do NOT rescore]")
        blocks.append({"type": "text", "text":
                       f"### Styled keyframe at t={a['timestamp_sec']}s "
                       f"({a['anchor_id']}): {tag}\nimage_url: {a['keyframe_url']}"})
        blocks.append(_image_block(a["keyframe_url"]))

    # rounds >= 2: compact driver-built history block before the rubric (§11.2, Q37)
    if round_idx >= 2 and history_summary:
        blocks.append({"type": "text", "text":
                       f"## Prior rounds summary\n{history_summary}"})

    # 7. rubric + thresholds + schema + output instruction LAST (Q38)
    blocks.append({"type": "text", "text":
                   "## Rubric, thresholds, and output schema\n"
                   "Score each `[NEW]` styled keyframe on all 5 dimensions (1-5 each): "
                   "geometry and composition vs the raw frame for the same anchor; medium, "
                   "palette, and line_quality vs the style reference.\n"
                   "Thresholds (driver-enforced): geometry = 5 required; composition = 5 "
                   "required; medium = 5 required; line_quality = 5 required; "
                   "palette >= 4 required.\n"
                   "Score only `[NEW]` keyframes; do NOT rescore `[previously approved]` "
                   "ones (coherence context only). Always include "
                   "cross_anchor_coherence_notes.\n"
                   f"Output JSON schema:\n{json.dumps(EVALUATOR_OUTPUT_SCHEMA, indent=2)}\n"
                   f"Set \"round\" to {round_idx}. Return exactly one JSON object matching "
                   "the schema; no prose outside the JSON."})
    return blocks


# ---------------------------------------------------------------------------
# Scoring: parse + driver-side pass/fail recomputation (Q20, §3)
# ---------------------------------------------------------------------------

def parse_evaluator_json(text: str) -> dict:
    """Strictly parse + validate the evaluator's JSON output (plan §4)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):  # tolerate fences, still strict otherwise
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from e

    for key in ("pass", "round", "keyframes", "cross_anchor_coherence_notes", "summary"):
        if key not in data:
            raise ValueError(f"missing top-level key: {key}")
    if not isinstance(data["keyframes"], list):
        raise ValueError("keyframes must be an array")
    for kf in data["keyframes"]:
        for key in ("anchor_id", "timestamp_sec", "image_url", "scores",
                    "pass", "failed_dimensions", "suggested_focus"):
            if key not in kf:
                raise ValueError(f"keyframe entry missing key: {key}")
        if kf["anchor_id"] not in ANCHOR_IDS:
            raise ValueError(f"unknown anchor_id: {kf['anchor_id']}")
        scores = kf["scores"]
        for dim in ALL_DIMENSIONS:
            if dim not in scores:
                raise ValueError(f"{kf['anchor_id']}: missing dimension {dim}")
            s = scores[dim]
            if not isinstance(s, dict) or "score" not in s or "rationale" not in s:
                raise ValueError(f"{kf['anchor_id']}.{dim}: must be {{score, rationale}}")
            if not isinstance(s["score"], int) or not 1 <= s["score"] <= 5:
                raise ValueError(f"{kf['anchor_id']}.{dim}.score must be int 1-5")
    return data


def scores_pass(keyframes: list[dict]) -> tuple[bool, list[dict]]:
    """Driver-side pass/fail recomputation from thresholds (plan §3, Q20).

    Returns (overall_pass, failures) where failures is a list of
    {anchor_id, failed_dimensions, suggested_focus}.
    """
    failures: list[dict] = []
    for kf in keyframes:
        failed: list[str] = []
        for dim, minimum in {**HARD_GATE_MIN, **SOFT_GATE_MIN}.items():
            if kf["scores"][dim]["score"] < minimum:
                failed.append(dim)
        if failed:
            failures.append({"anchor_id": kf["anchor_id"],
                             "failed_dimensions": failed,
                             "suggested_focus": kf.get("suggested_focus", "")})
    return (not failures, failures)


# ---------------------------------------------------------------------------
# Evaluator round (plan §13 run_evaluator_round; Q14/Q15 fresh session; Q20 retry)
# ---------------------------------------------------------------------------

def run_evaluator_round(client: ArkClient, agent_id: str, env_id: str,
                        round_idx: int, anchors: list[dict],
                        style_image_url: str | None, style_prompt: str | None,
                        history_summary: str | None = None) -> dict:
    """Run one Evaluator round: fresh session, single visual prompt, strict JSON.

    Returns {session_id, round, result, pass, failures,
             cross_anchor_coherence_notes, summary}. `pass`/`failures` are
    driver-recomputed from thresholds, NOT the model's booleans (Q20).
    Raises EvaluatorInfraError on infra failure (incl. double parse failure).
    """
    session_id = create_session(client, agent_id, env_id)
    blocks = build_evaluator_message(round_idx, anchors,
                                     style_image_url, style_prompt, history_summary)
    send_message(client, session_id, blocks)

    parsed: dict | None = None
    for attempt in range(2):  # initial + one schema-retry nudge (Q20)
        events = poll_until_idle(client, session_id)
        text = extract_assistant_text(events)
        try:
            parsed = parse_evaluator_json(text)
            break
        except ValueError as e:
            if attempt == 0:
                send_message(client, session_id,
                             [{"type": "text", "text": SCHEMA_RETRY_NUDGE}])
            else:
                # Debug: dump raw evaluator output to file for inspection
                import os
                dump_path = os.path.join(os.environ.get("WORK_DIR", "work"), "evaluator_raw_output.txt")
                with open(dump_path, "w") as df:
                    df.write(text)
                print(f"[DEBUG] Raw evaluator output dumped to {dump_path} ({len(text)} chars)", flush=True)
                raise EvaluatorInfraError(
                    "evaluator_parse",
                    f"evaluator output invalid after schema-retry nudge: {e}") from e
    assert parsed is not None

    ok, failures = scores_pass(parsed["keyframes"])
    return {
        "session_id": session_id,
        "round": round_idx,
        "result": parsed,
        "pass": ok,
        "failures": failures,
        "cross_anchor_coherence_notes": parsed["cross_anchor_coherence_notes"],
        "summary": parsed["summary"],
    }


# ---------------------------------------------------------------------------
# Standalone CLI (one evaluator round; the outer 3-attempt loop is driver scope)
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run one Keyframe QC Evaluator round (plan §4/§11).")
    ap.add_argument("--anchors-json", required=True,
                    help='JSON file: {"anchors":[{anchor_id,timestamp_sec,'
                         'raw_frame_url,keyframe_url,status:"new"|"approved"}]}')
    ap.add_argument("--round", type=int, default=1, dest="round_idx")
    ap.add_argument("--style-image-url", default=None)
    ap.add_argument("--style-prompt", default=None)
    ap.add_argument("--history-summary", default=None,
                    help="Driver-built compact history block (rounds >= 2, Q37)")
    ap.add_argument("--ark-api-key", default=os.environ.get("ARK_API_KEY"))
    ap.add_argument("--evaluator-agent-id", default=None,
                    help="Override; skips bootstrap (§10.4)")
    ap.add_argument("--environment-id", default=None,
                    help="Override; skips bootstrap (§10.4)")
    ap.add_argument("--report-path", default=None,
                    help="Write pretty JSON result to this path")
    args = ap.parse_args()

    if not args.ark_api_key:
        print("error: --ark-api-key or $ARK_API_KEY required", file=sys.stderr)
        return 3
    if not args.style_image_url and not args.style_prompt:
        print("error: at least one of --style-image-url/--style-prompt required (Q17)",
              file=sys.stderr)
        return 3

    with open(args.anchors_json) as f:
        anchors = json.load(f)["anchors"]

    client = ArkClient(args.ark_api_key)
    try:
        agent_id, agent_version, env_id = bootstrap_evaluator(
            client, args.evaluator_agent_id, args.environment_id)
        out = run_evaluator_round(client, agent_id, env_id, args.round_idx, anchors,
                                  args.style_image_url, args.style_prompt,
                                  args.history_summary)
        out["version"] = {"qc_gate_evaluator": VERSION,
                          "evaluator_agent": f"{agent_id}@v{agent_version}"}
        if args.report_path:
            with open(args.report_path, "w") as f:
                json.dump(out, f, indent=2)
        print(json.dumps(out, separators=(",", ":")))  # compact single line (Q29)
        return 0 if out["pass"] else 2
    except EvaluatorInfraError as e:
        err = {"status": "infrastructure_error", "stage": e.stage, "error": e.error}
        if args.report_path:
            with open(args.report_path, "w") as f:
                json.dump(err, f, indent=2)
        print(json.dumps(err, separators=(",", ":")))
        return 3


if __name__ == "__main__":
    sys.exit(main())
