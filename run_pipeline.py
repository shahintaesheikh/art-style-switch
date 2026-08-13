#!/usr/bin/env python3
"""Art-style-switch pipeline — end-to-end runner (plan §15).

Hosting model: local files are processed locally (ffmpeg / base64). The style
reference image is passed everywhere as a base64 data URI (VLM analysis,
Seedream i2i, and Seedance reference_image — Seedance 2.0 supports base64
image input per docs §Limitations/Multimodal input). Only the input video
reference for Seedance still needs an asset:// URI (video input is URL/asset
only, base64 not supported for video).

QC gate: qc_gate.py is invoked as a subprocess between raw keyframe extraction
and Seedance. It runs Seedream round-0 + Evaluator/Reviser loop and writes
keyframes.json with QC-passed URLs. The pipeline reads those and feeds them
into Seedance.
"""

import json
import os
import subprocess
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

import pipeline  # noqa: F401 — truststore injection (corporate TLS interception)
from pipeline.audio import extract_audio
from pipeline.keyframes import anchor_timestamps, extract_frame
from pipeline.llm_gate import generate_style_prompts
from pipeline.mux import mux_audio
from pipeline.probe import probe
from pipeline.seedance import submit_task, wait_for_task

WORK = os.environ.get("WORK_DIR", "./work")
INPUT_VIDEO = os.environ["INPUT_VIDEO"]          # local path (ffmpeg/base64)
STYLE_REF = os.environ["STYLE_REF"]              # local path (base64)
INPUT_VIDEO_URI = os.environ["INPUT_VIDEO_URI"]  # asset://... (Seedance reference)


def download(url: str, out: str) -> str:
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return out


def main() -> None:
    os.makedirs(WORK, exist_ok=True)

    # 1-2. probe + preprocess (no-op copy: 560x752 is native 3:4@480p, plan §6)
    info = probe(INPUT_VIDEO)
    print("probe:", info)

    # 2a. LLM Gate — analyze reference style image and generate style-specific prompts
    #     The VLM receives the image as base64 inline (no asset upload needed)
    print("\n=== LLM Gate: analyzing reference style image ===")
    api_key = os.environ["ARK_API_KEY"]
    style_prompts = generate_style_prompts(api_key, STYLE_REF)
    print("  style_label:", style_prompts["style_analysis"].get("style_label", "unknown"))
    print("  medium:", style_prompts["style_analysis"].get("medium", "")[:80])
    print("  keyframe prompt:", len(style_prompts["keyframe"]), "chars")
    print("  seedance prompt:", len(style_prompts["seedance"]), "chars")
    KEYFRAME_PROMPT = style_prompts["keyframe"]
    SEEDANCE_PROMPT = style_prompts["seedance"]

    preprocessed = INPUT_VIDEO

    # 3. audio extraction (extract before stripping, so reference is silent)
    audio = extract_audio(preprocessed, f"{WORK}/audio.aac")
    print("audio:", audio)

    # 4. strip audio from working video → silent reference for Seedream/Seedance
    #    If INPUT_VIDEO is work/preprocessed.mp4, strip audio from that directly
    silent_video = f"{WORK}/preprocessed_silent.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        "-an", "-c:v", "copy", silent_video,
    ], check=True, capture_output=True)
    print("silent video:", silent_video)

    # 5. anchor keyframes (A0-A7 at 0%, ~14.3%, ~28.6%, ~42.9%, ~57.1%, ~71.4%, ~85.7%, 100%) — from SILENT video
    ts = anchor_timestamps(info["duration"])
    raws = [extract_frame(silent_video, t, f"{WORK}/kf{i}mono.jpg")
            for i, t in enumerate(ts, 0)]
    print("raw keyframes:", raws)

    # 6. QC Gate: Seedream round-0 → Evaluator → Reviser loop → QC-passed KFs
    #    Uses the SILENT video as input (no audio in the pipeline)
    kfs_json = os.path.join(WORK, "qc_keyframes.json")
    qc_report = os.path.join(WORK, "qc_report.json")
    anchor_ts = ",".join(str(t) for t in ts)

    # Skip QC if valid keyframes already exist (cached from previous run)
    skip_qc = False
    if os.path.exists(kfs_json):
        try:
            with open(kfs_json) as f:
                cached = json.load(f)
            if cached.get("status") == "passed" and all(
                kf.get("image_url") for kf in cached.get("keyframes", [])
            ):
                skip_qc = True
                print(f"=== QC Gate: using cached keyframes ({kfs_json}) ===")
        except (json.JSONDecodeError, KeyError):
            pass

    if not skip_qc:
        print("=== QC Gate (silent video) ===")
        result = subprocess.run(
            [sys.executable, "qc_gate.py",
             "--raw-video", silent_video,
             "--style-image", STYLE_REF,
             "--style-prompt", KEYFRAME_PROMPT,
             "--anchor-timestamps", anchor_ts,
             "--output-kfs-json", kfs_json,
             "--report-path", qc_report],
            capture_output=True, text=True, timeout=2700,
        )
        if result.stdout:
            last_line = result.stdout.strip().split("\n")[-1]
            print("qc_gate:", last_line[:200], "..." if len(last_line) > 200 else "")
        if result.returncode != 0:
            print("qc_gate stderr:", result.stderr, file=sys.stderr)
            reason = "infra error" if result.returncode == 3 else "QC failed after 3 attempts"
            raise SystemExit(f"QC gate exit code {result.returncode} — {reason}")

    # Read QC-passed keyframes
    with open(kfs_json) as f:
        kf_data = json.load(f)

    # Extract URLs: all interior anchors (A1-A7) passed to Seedance
    kf_urls = [kf["image_url"] for kf in kf_data["keyframes"]
               if kf["anchor_id"] != "A0"]
    print("QC-passed keyframe URLs:", kf_urls)

    # 7. Seedance — silent reference → silent output (no VLM gate)
    print("\n=== Seedance generation ===")
    task_id = submit_task(STYLE_REF, kf_urls, INPUT_VIDEO_URI,
                          duration=round(info["duration"]), seed=42,
                          prompt=SEEDANCE_PROMPT)
    print("Seedance task:", task_id)
    stylized_url = wait_for_task(task_id)
    silent = download(stylized_url, f"{WORK}/stylized_silent.mp4")
    print("stylized silent:", silent)

    # 8. audio mux → final deliverable (re-mux the pre-extracted audio)
    final = mux_audio(silent, audio, f"{WORK}/unity_handdrawn_final.mp4")
    print("DONE →", final)
    print("final probe:", probe(final))


if __name__ == "__main__":
    main()
