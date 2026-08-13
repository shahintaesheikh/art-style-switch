#!/usr/bin/env python3
"""Keyframe QC Gate — v0.1.0 (plan: .pi/qc_gate_worker_plan.md)

Single-file driver that sits between raw video + style reference and Seedance
video-generation. It:
  1. Extracts 3 anchor frames (ffmpeg) and runs Seedream i2i to produce baseline
     styled keyframes (round-0).
  2. Sends KFs to an Evaluator Managed Agent (vision model, no tools) that scores
     them on a 5-dimension rubric. Driver recomputes pass/fail from thresholds.
  3. If all anchors pass → writes keyframes.json, exit 0.
  4. If any anchor fails → spins up a Reviser Managed Agent (3 custom tools:
     extract_keyframe, generate_keyframe, submit_revision) to regenerate failing
     KFs.
  5. Loops back to step 2. After 3 cycles, halts with QC report, exit 2.

Imports reusable building blocks from evaluator.py and reviser.py (the two
Managed Agent implementations). This file owns the outer loop, round-0 bootstrap,
inter-round history, reporting, and exit codes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import from the two Managed Agent implementations
# ---------------------------------------------------------------------------

from evaluator import (
    ArkClient,
    bootstrap_evaluator,
    run_evaluator_round,
    scores_pass,
    ANCHOR_IDS as EVAL_ANCHOR_IDS,
    EVALUATOR_SYSTEM_PROMPT,
    EVALUATOR_OUTPUT_SCHEMA,
    SCHEMA_RETRY_NUDGE,
    EvaluatorInfraError,
)

from reviser import (
    InfraError as ReviserInfraError,
    Resolved,
    resolve_input,
    resolve_asset,
    download,
    ffprobe_duration,
    ffmpeg_extract_frame,
    b64_jpeg,
    data_uri,
    image_block_b64,
    validate_jpeg_aspect,
    download_as_jpeg_b64,
    seedream_i2i,
    bootstrap_reviser,
    run_reviser_round,
    upload_skill_zip,
    ANCHOR_IDS as REVISER_ANCHOR_IDS,
    SEEDREAM_MODEL_ID,
    REVISER_SYSTEM_PROMPT,
    REVISER_TOOLS,
    REVISER_MAX_TURNS,
    REVISER_MAX_KF_PER_ANCHOR,
    REVISER_MAX_KF_TOTAL,
    REVISER_WALL_CLOCK_SEC,
)

# --verify ANCHOR_IDS match across modules
assert EVAL_ANCHOR_IDS == REVISER_ANCHOR_IDS, "ANCHOR_IDS mismatch across evaluator/reviser"
ANCHOR_IDS = EVAL_ANCHOR_IDS

# ---------------------------------------------------------------------------
# Module-level constants (plan §8)
# ---------------------------------------------------------------------------

VERSION = "0.1.0"
INFERENCE_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# Rubric thresholds (plan §3)
HARD_GATE_MIN = {"geometry": 5, "composition": 5, "medium": 5, "line_quality": 5}
SOFT_GATE_MIN = {"palette": 4}

# Caps
MAX_QC_ATTEMPTS = 3
DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024
FFMPEG_QV = 2

# Round-0 seed prompt (plan §8 — driver-pinned, conservative)
ROUND0_SEED_PROMPT = (
    "Apply the provided style reference to this raw video frame to produce "
    "a styled keyframe. Preserve the exact geometry, object count, positions, "
    "orientations, camera angle, and vanishing point of every element in the "
    "original frame 1:1. Do not add, remove, move, rotate, resize, or morph "
    "any object. Maintain the same framing, zoom level, and 3:4 aspect ratio "
    "as the original — no letterboxing, no cropping. Match the style "
    "reference's medium, palette, and line_quality faithfully.\n"
    "Pay special attention to the RED PLAYER CAR: its shape, silhouette, "
    "and shading must be preserved exactly as in the raw frame. The car roof "
    "must be clearly distinguishable from the road surface — do not let the "
    "car body merge with the road or background. The red car must remain a "
    "solid, recognizable shape with consistent red colored-pencil shading "
    "that matches the reference's crimson tone exactly. The car's outline, "
    "wheel wells, windows, and body panels must be distinctly rendered and "
    "not blend into surrounding elements.\n"
    "BUILDINGS must be consistently rendered in the hand-drawn pencil "
    "sketch style of the reference: graphite hatching on building facades, "
    "colored-pencil squares for windows, and sketchy pencil outlines for "
    "edges. Do not let buildings become flat, solid color blocks — every "
    "building surface must show visible pencil stroke texture and hatching "
    "that matches the reference's drawing style exactly. Building windows "
    "must be distinct colored-pencil squares, not smudged or blurred."
)

# Round-0 negative prompt (plan §8)
ROUND0_NEGATIVE_PROMPT = (
    "morphing, distorted geometry, extra objects, flickering objects, missing objects, rotated "
    "objects, changed object count, changed orientation, shifted vanishing "
    "point, letterboxing, watermark, cropped, zoomed in, zoomed out, wrong "
    "aspect ratio, blurry, low resolution"
)


# ---------------------------------------------------------------------------
# Argument parsing (plan §7)
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Keyframe QC Gate v0.1.0 — quality gate between styled "
                    "keyframe generation and Seedance video generation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--raw-video", required=True,
                    help="Local path / https:// URL / asset:// URI (plan §34)")
    ap.add_argument("--style-image", default=None,
                    help="Style reference image (same schemes)")
    ap.add_argument("--style-prompt", default=None,
                    help="Style prompt fallback (used only if no style image)")
    ap.add_argument("--anchor-timestamps", default=None,
                    help="3 comma-separated floats (seconds); defaults to "
                         "0%%,33%%,66%% of duration")
    ap.add_argument("--ark-api-key",
                    default=os.environ.get("ARK_API_KEY"),
                    help="ModelArk API key (default: $ARK_API_KEY)")
    ap.add_argument("--ark-access-key",
                    default=os.environ.get("ARK_ACCESS_KEY"),
                    help="Access key for asset:// resolution (default: $ARK_ACCESS_KEY)")
    ap.add_argument("--ark-secret-key",
                    default=os.environ.get("ARK_SECRET_KEY"),
                    help="Secret key for asset:// resolution (default: $ARK_SECRET_KEY)")
    ap.add_argument("--evaluator-agent-id", default=None,
                    help="Override auto-bootstrapped Evaluator agent ID")
    ap.add_argument("--reviser-agent-id", default=None,
                    help="Override auto-bootstrapped Reviser agent ID")
    ap.add_argument("--environment-id", default=None,
                    help="Override auto-bootstrapped Environment ID")
    ap.add_argument("--seedream-prompt-skill-id", default=None,
                    help="ModelArk Skill ID for the Seedream prompt-engineering "
                         "skill (attached to Reviser agent)")
    ap.add_argument("--skill-zip", action="append", default=[],
                    help="Path to a custom Skill zip file to upload and attach to the "
                         "Reviser agent. Can be specified multiple times. "
                         "Example: --skill-zip ./path/to/skill.zip")
    ap.add_argument("--report-path", default=None,
                    help="Write pretty-printed QC report JSON to this path")
    ap.add_argument("--output-kfs-json", required=True,
                    help="Write Seedance-ready keyframes JSON to this path (on pass)")
    ap.add_argument("--ffmpeg-path", default="ffmpeg",
                    help="ffmpeg binary path (default: ffmpeg on PATH)")
    ap.add_argument("--ffprobe-path", default="ffprobe",
                    help="ffprobe binary path (default: ffprobe on PATH)")
    args = ap.parse_args(argv)

    if not args.ark_api_key:
        ap.error("--ark-api-key or $ARK_API_KEY is required")
    if not args.style_image and not args.style_prompt:
        ap.error("at least one of --style-image / --style-prompt is required")

    return args


# ---------------------------------------------------------------------------
# Round-0 bootstrap: ffmpeg extract + Seedream i2i × 3 (parallel)
# ---------------------------------------------------------------------------


def _compute_anchor_timestamps(duration: float,
                               custom: str | None) -> list[float]:
    """Compute anchor timestamps (plan Q42).

    Defaults to 0%, ~14.3%, ~28.6%, ~42.9%, ~57.1%, ~71.4%, ~85.7%, 100% of duration (8 anchors).
    Custom: comma-separated floats.
    """
    if custom:
        parts = [float(x.strip()) for x in custom.split(",")]
        if len(parts) != len(ANCHOR_IDS):
            raise ValueError(
                f"--anchor-timestamps must have exactly {len(ANCHOR_IDS)} floats, "
                f"got {len(parts)}")
        return parts
    return [0.0, duration * 0.25, duration * 0.50, duration * 0.75, duration * 1.0]


def _round0_seedream(anchor_id: str, raw_frame: Path, style: Resolved | None,
                     style_prompt: str | None, seed: int,
                     api_key: str, ffmpeg: str) -> dict:
    """One round-0 Seedream i2i call. Returns {url, seed, prompt_used, ...}.

    Uses the dynamic ``style_prompt`` from the LLM Gate when provided;
    falls back to the hardcoded ``ROUND0_SEED_PROMPT`` only if no
    style_prompt is given (legacy behavior).
    """
    prompt = style_prompt if style_prompt else ROUND0_SEED_PROMPT
    return seedream_i2i(
        raw_frame, style, prompt, ROUND0_NEGATIVE_PROMPT,
        seed, api_key,
    )


def bootstrap_round0(args: argparse.Namespace, tmp: Path,
                     ) -> tuple[dict[str, Any], list[float], float]:
    """Round-0: resolve inputs, ffprobe duration, extract raw frames, parallel
    Seedream i2i.

    Returns (keyframes_by_anchor: dict, anchor_timestamps: list[float], duration: float).
    keyframes_by_anchor[anchor_id] = {
        "anchor_id", "timestamp_sec", "image_url", "prompt_used",
        "negative_prompt_used", "seed", "raw_frame_path"
    }
    """
    # Resolve inputs
    raw_video = resolve_input(args.raw_video, args, tmp, "round0")
    style = (resolve_input(args.style_image, args, tmp, "round0_style")
             if args.style_image else None)

    # Download video if needed
    video_path = (raw_video.path if raw_video.kind == "path"
                  else download(raw_video.url, tmp / "input_video.mp4", "round0_dl"))

    duration = ffprobe_duration(video_path, args.ffprobe_path)
    timestamps = _compute_anchor_timestamps(duration, args.anchor_timestamps)

    # Extract raw frames
    raw_frames: dict[str, Path] = {}
    for i, (anchor_id, ts) in enumerate(zip(ANCHOR_IDS, timestamps)):
        out = tmp / f"{anchor_id}_raw.jpg"
        ffmpeg_extract_frame(video_path, ts, out, args.ffmpeg_path)
        raw_frames[anchor_id] = out

    # Parallel Seedream i2i (round-0 seeds: all 42 for consistency)
    seeds = [42, 42, 42, 42, 42, 42, 42, 42]
    keyframes: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_map = {}
        for i, anchor_id in enumerate(ANCHOR_IDS):
            fut = ex.submit(_round0_seedream, anchor_id, raw_frames[anchor_id],
                            style, args.style_prompt, seeds[i],
                            args.ark_api_key, args.ffmpeg_path)
            fut_map[fut] = anchor_id
        for fut in fut_map:
            anchor_id = fut_map[fut]
            result = fut.result()
            keyframes[anchor_id] = {
                "anchor_id": anchor_id,
                "timestamp_sec": timestamps[ANCHOR_IDS.index(anchor_id)],
                "image_url": result["url"],
                "keyframe_url": result["url"],
                "prompt_used": result["prompt_used"],
                "negative_prompt_used": result["negative_prompt_used"],
                "seed": result["seed"],
                "raw_frame_path": str(raw_frames[anchor_id]),
            }

    return keyframes, timestamps, duration


# ---------------------------------------------------------------------------
# Inter-round history builder (plan Q37: deterministic, ~100-300 tokens)
# ---------------------------------------------------------------------------


def build_history_summary(rounds: list[dict]) -> str:
    """Compact driver-built JSON summary of prior rounds (plan Q37).

    Each round entry: {round, failures, coherence_notes, reviser_delta, driver_forced}
    """
    history = []
    for r in rounds:
        entry = {
            "round": r.get("round", 0),
            "failures": [
                {"anchor_id": f["anchor_id"],
                 "failed_dimensions": f["failed_dimensions"]}
                for f in r.get("failures", [])
            ],
            "coherence_notes": r.get("cross_anchor_coherence_notes", ""),
            "driver_forced": r.get("delta", {}).get("driver_forced", False),
        }
        if r.get("delta"):
            regen = r["delta"].get("regenerated", [])
            if regen:
                entry["revisions"] = [
                    {"anchor_id": x["anchor_id"],
                     "changes_made": x.get("changes_made", "")}
                    for x in regen
                ]
        history.append(entry)
    return json.dumps(history, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Report writer (plan §9)
# ---------------------------------------------------------------------------


def build_report(status: str, args: argparse.Namespace,
                 timestamps: list[float],
                 keyframes: dict[str, Any],
                 rounds: list[dict],
                 duration_sec: float,
                 total_kf_generations: int,
                 failure_reason: str | None = None,
                 infra_error: dict | None = None) -> dict:
    """Build the structured QC report (plan §9 schema)."""
    report: dict[str, Any] = {
        "status": status,
        "version": {
            "qc_gate": VERSION,
            "evaluator_agent": None,
            "reviser_agent": None,
        },
        "video": args.raw_video,
        "style": {
            "image_url": args.style_image,
            "prompt": args.style_prompt,
        },
        "anchor_timestamps_sec": timestamps,
        "final_keyframes": [
            {
                "anchor_id": a,
                "timestamp_sec": keyframes[a].get("timestamp_sec", timestamps[i]),
                "keyframe_url": keyframes[a].get("image_url", keyframes[a].get("keyframe_url", "")),
                "prompt_used": keyframes[a].get("prompt_used", ""),
                "negative_prompt_used": keyframes[a].get("negative_prompt_used", ""),
                "seed": keyframes[a].get("seed", 0),
            }
            for i, a in enumerate(ANCHOR_IDS) if a in keyframes
        ],
        "seedance_input_ready": status == "passed",
        "rounds": rounds,
        "failure_reason": failure_reason,
        "infrastructure_error": infra_error,
        "duration_seconds": round(duration_sec, 1),
        "total_kf_generations": total_kf_generations,
    }

    # Extract agent versions from first round if available
    if rounds:
        r0 = rounds[0]
        if "evaluator_session_id" in r0:
            report["version"]["evaluator_agent"] = r0.get("evaluator_agent_id", "unknown")
        if "reviser_session_id" in r0:
            report["version"]["reviser_agent"] = r0.get("reviser_agent_id", "unknown")

    return report


def write_report(report: dict, report_path: str | None) -> None:
    """Write pretty-printed report JSON to --report-path (if given) and always
    print a compact single-line JSON to stdout (plan Q29)."""
    if report_path:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    # Compact single-line stdout (pipeline-friendly)
    print(json.dumps(report, separators=(",", ":"), ensure_ascii=False))


def write_output_kfs(keyframes: dict[str, Any], timestamps: list[float],
                     args: argparse.Namespace, qc_rounds_taken: int,
                     report_path: str | None) -> None:
    """Write the Seedance-ready keyframes JSON (plan output schema, exit 0 only)."""
    output = {
        "status": "passed",
        "keyframes": [
            {
                "anchor_id": a,
                "timestamp_sec": keyframes[a].get("timestamp_sec", timestamps[i]),
                "image_url": keyframes[a].get("image_url", keyframes[a].get("keyframe_url", "")),
                "prompt_used": keyframes[a].get("prompt_used", ""),
                "negative_prompt_used": keyframes[a].get("negative_prompt_used", ""),
                "seed": keyframes[a].get("seed", 0),
            }
            for i, a in enumerate(ANCHOR_IDS) if a in keyframes
        ],
        "aspect_ratio": "3:4",
        "qc_rounds_taken": qc_rounds_taken,
        "qc_report_path": report_path or "",
    }
    with open(args.output_kfs_json, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Outer loop: evaluate/revise (max 3 cycles, plan §6 state machine)
# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    start_time = time.time()

    # One temp directory for the entire run (plan §46)
    with tempfile.TemporaryDirectory(prefix="kf-qc-") as tmpdir:
        tmp = Path(tmpdir)

        # ---- Round-0: bootstrap keyframes ---------------------------------
        try:
            keyframes, timestamps, duration = bootstrap_round0(args, tmp)
        except (ReviserInfraError, EvaluatorInfraError) as e:
            stage = getattr(e, "stage", "round0")
            err = {"stage": stage, "error": str(e)}
            report = build_report("infrastructure_error", args, [], {}, [],
                                  time.time() - start_time, 0,
                                  infra_error=err)
            write_report(report, args.report_path)
            return 3
        except Exception as e:
            err = {"stage": "round0", "error": f"unexpected error: {e}"}
            report = build_report("infrastructure_error", args, [], {}, [],
                                  time.time() - start_time, 0,
                                  infra_error=err)
            write_report(report, args.report_path)
            return 3

        total_kf_generations = 8  # round-0 created 8 KFs
        rounds: list[dict] = []
        best_keyframes = dict(keyframes)  # always keep best-so-far

        # ---- Evaluate/Revise loop (max 3 cycles) --------------------------
        for attempt in range(1, MAX_QC_ATTEMPTS + 1):
            # Build anchor list for Evaluator (plan §11.1 / §11.2)
            is_first_round = (attempt == 1)
            eval_anchors = []
            for a in ANCHOR_IDS:
                entry = {
                    "anchor_id": a,
                    "timestamp_sec": keyframes[a].get("timestamp_sec", timestamps[ANCHOR_IDS.index(a)]),
                    "raw_frame_url": keyframes[a].get("raw_frame_path", keyframes[a].get("image_url", "")),
                    "keyframe_url": keyframes[a].get("image_url", keyframes[a].get("keyframe_url", "")),
                    "status": "new" if is_first_round else "approved",
                }
                # Determine if this anchor was regenerated in the previous round
                if not is_first_round and rounds:
                    prev_delta = rounds[-1].get("delta", {})
                    regen_ids = {r["anchor_id"] for r in prev_delta.get("regenerated", [])}
                    entry["status"] = "new" if a in regen_ids else "approved"
                eval_anchors.append(entry)

            # Build history summary (rounds ≥ 2, plan Q37)
            history_summary = build_history_summary(rounds) if not is_first_round else None

            # ---- EVALUATOR ROUND ------------------------------------------
            try:
                # Bootstrap evaluator agent (cached or create)
                client = ArkClient(args.ark_api_key)
                eval_agent_id, eval_agent_version, env_id = bootstrap_evaluator(
                    client,
                    args.evaluator_agent_id,
                    args.environment_id,
                )
                eval_result = run_evaluator_round(
                    client, eval_agent_id, env_id, attempt,
                    eval_anchors, args.style_image, args.style_prompt,
                    history_summary,
                )
            except (ReviserInfraError, EvaluatorInfraError) as e:
                stage = getattr(e, "stage", "evaluator")
                err = {"stage": stage, "error": str(e)}
                report = build_report("infrastructure_error", args, timestamps,
                                      best_keyframes, rounds,
                                      time.time() - start_time,
                                      total_kf_generations,
                                      infra_error=err)
                write_report(report, args.report_path)
                return 3
            except Exception as e:
                err = {"stage": "evaluator", "error": f"unexpected error: {e}"}
                report = build_report("infrastructure_error", args, timestamps,
                                      best_keyframes, rounds,
                                      time.time() - start_time,
                                      total_kf_generations,
                                      infra_error=err)
                write_report(report, args.report_path)
                return 3

            # Promoted approved anchors back into best_keyframes
            for kf in eval_result.get("result", {}).get("keyframes", []):
                a = kf["anchor_id"]
                if kf.get("pass"):
                    best_keyframes[a] = {**best_keyframes.get(a, {}),
                                         "image_url": kf["image_url"],
                                         "keyframe_url": kf["image_url"]}

            # Driver-side threshold check
            eval_pass = eval_result["pass"]
            failures = eval_result["failures"]

            # Record round
            round_entry = {
                "round": attempt,
                "evaluator_session_id": eval_result["session_id"],
                "evaluator_agent_id": f"{eval_agent_id}@v{eval_agent_version}",
                "scores_per_kf": {
                    kf["anchor_id"]: kf["scores"]
                    for kf in eval_result.get("result", {}).get("keyframes", [])
                },
                "pass": eval_pass,
                "failures": failures,
                "cross_anchor_coherence_notes": eval_result.get("cross_anchor_coherence_notes", ""),
                "reviser_session_id": None,
                "reviser_actions": [],
                "driver_forced": False,
            }
            rounds.append(round_entry)

            if eval_pass:
                # All anchors passed — write output and exit 0
                write_output_kfs(best_keyframes, timestamps, args, attempt,
                                 args.report_path)
                report = build_report("passed", args, timestamps, best_keyframes,
                                      rounds, time.time() - start_time,
                                      total_kf_generations)
                write_report(report, args.report_path)
                return 0

            if attempt >= MAX_QC_ATTEMPTS:
                # Exhausted retries — exit 2
                report = build_report("failed", args, timestamps, best_keyframes,
                                      rounds, time.time() - start_time,
                                      total_kf_generations,
                                      failure_reason="attempts_exhausted")
                write_report(report, args.report_path)
                return 2

            # ---- REVISER ROUND --------------------------------------------
            # Build the graded rubric JSON as the reviser expects it
            graded_rubric = {
                "pass": eval_pass,
                "round": attempt,
                "keyframes": eval_result.get("result", {}).get("keyframes", []),
                "cross_anchor_coherence_notes": eval_result.get("cross_anchor_coherence_notes", ""),
                "summary": eval_result.get("summary", ""),
            }

            # Build current keyframes JSON for the reviser
            current_kfs = {
                a: {
                    "anchor_id": a,
                    "timestamp_sec": keyframes[a].get("timestamp_sec", timestamps[ANCHOR_IDS.index(a)]),
                    "image_url": keyframes[a].get("image_url", keyframes[a].get("keyframe_url", "")),
                    "keyframe_url": keyframes[a].get("image_url", keyframes[a].get("keyframe_url", "")),
                    "prompt_used": keyframes[a].get("prompt_used", ""),
                    "negative_prompt_used": keyframes[a].get("negative_prompt_used", ""),
                    "seed": keyframes[a].get("seed", 0),
                }
                for a in ANCHOR_IDS if a in keyframes
            }

            # Write graded rubric and current KFs to temp files for reviser
            rubric_path = tmp / "graded_rubric.json"
            rubric_path.write_text(json.dumps(graded_rubric, indent=2))
            kfs_path = tmp / "current_keyframes.json"
            kfs_path.write_text(json.dumps(current_kfs, indent=2))

            # Build history JSON for reviser
            history_path = None
            if len(rounds) > 1:
                history_path = tmp / "reviser_history.json"
                history_path.write_text(build_history_summary(rounds[:-1]))

            # Create a temporary args-like object for the reviser
            rev_args = argparse.Namespace(
                raw_video=args.raw_video,
                style_image=args.style_image,
                style_prompt=args.style_prompt,
                ark_api_key=args.ark_api_key,
                ark_access_key=args.ark_access_key,
                ark_secret_key=args.ark_secret_key,
                reviser_agent_id=args.reviser_agent_id,
                environment_id=args.environment_id,
                seedream_prompt_skill_id=args.seedream_prompt_skill_id,
                skill_zip=args.skill_zip,
                ffmpeg_path=args.ffmpeg_path,
                ffprobe_path=args.ffprobe_path,
                # The reviser round uses these named params via its own args
                graded_rubric=str(rubric_path),
                current_keyframes=str(kfs_path),
                history_json=str(history_path) if history_path else None,
            )

            try:
                rev_result = run_reviser_round(rev_args)
            except (ReviserInfraError, EvaluatorInfraError) as e:
                stage = getattr(e, "stage", "reviser")
                err = {"stage": stage, "error": str(e)}
                report = build_report("infrastructure_error", args, timestamps,
                                      best_keyframes, rounds,
                                      time.time() - start_time,
                                      total_kf_generations,
                                      infra_error=err)
                write_report(report, args.report_path)
                return 3
            except Exception as e:
                err = {"stage": "reviser", "error": f"unexpected error: {e}"}
                report = build_report("infrastructure_error", args, timestamps,
                                      best_keyframes, rounds,
                                      time.time() - start_time,
                                      total_kf_generations,
                                      infra_error=err)
                write_report(report, args.report_path)
                return 3

            # Merge reviser output into keyframes
            delta = rev_result.get("delta", {})
            merged = rev_result.get("merged_keyframes", [])
            for entry in merged:
                a = entry["anchor_id"]
                keyframes[a] = {
                    **keyframes.get(a, {}),
                    "anchor_id": a,
                    "timestamp_sec": entry.get("timestamp_sec", timestamps[ANCHOR_IDS.index(a)]),
                    "image_url": entry.get("image_url", entry.get("keyframe_url", "")),
                    "keyframe_url": entry.get("image_url", entry.get("keyframe_url", "")),
                    "prompt_used": entry.get("prompt_used", ""),
                    "negative_prompt_used": entry.get("negative_prompt_used", ""),
                    "seed": entry.get("seed", 0),
                }

            # Update best_keyframes with regenerated anchors
            for entry in merged:
                a = entry["anchor_id"]
                best_keyframes[a] = dict(keyframes[a])

            # Track KF generations from reviser
            reviser_kf_count = len(delta.get("regenerated", []))
            total_kf_generations += reviser_kf_count

            # Update round entry with reviser info
            round_entry["reviser_session_id"] = rev_result.get("reviser_session_id")
            round_entry["reviser_actions"] = rev_result.get("reviser_actions", [])
            round_entry["delta"] = delta
            round_entry["driver_forced"] = delta.get("driver_forced", False)

            # Continue to next iteration of the loop
            continue

        # Should not reach here — loop returns from inside
        report = build_report("failed", args, timestamps, best_keyframes,
                              rounds, time.time() - start_time,
                              total_kf_generations,
                              failure_reason="attempts_exhausted")
        write_report(report, args.report_path)
        return 2


if __name__ == "__main__":
    sys.exit(main())