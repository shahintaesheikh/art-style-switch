#!/usr/bin/env python3
"""Unity hand-drawn style pipeline — end-to-end runner (plan §15).

Hosting model: local files are processed locally (ffmpeg / base64); the two
inputs Seedance needs as references live in the ModelArk Asset Library
(uploaded once via console) and are referenced by asset:// URIs.

QC gate: qc_gate.py is invoked as a subprocess between raw keyframe extraction
and Seedance. It runs Seedream round-0 + Evaluator/Reviser loop and writes
keyframes.json with QC-passed URLs. The pipeline reads those and feeds them
into Seedance.
"""

import json
import os
import shutil
import subprocess
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

import pipeline  # noqa: F401 — truststore injection (corporate TLS interception)
from pipeline.audio import extract_audio
from pipeline.keyframes import anchor_timestamps, extract_frame
from pipeline.mux import mux_audio
from pipeline.probe import probe
from pipeline.seedance import submit_task, wait_for_task

WORK = os.environ.get("WORK_DIR", "./work")
INPUT_VIDEO = os.environ["INPUT_VIDEO"]          # local path (ffmpeg/base64)
STYLE_REF = os.environ["STYLE_REF"]              # local path (base64)
INPUT_VIDEO_URI = os.environ["INPUT_VIDEO_URI"]  # asset://... (Seedance reference)
STYLE_REF_URI = os.environ["STYLE_REF_URI"]      # asset://... (Seedance reference)


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
    preprocessed = f"{WORK}/preprocessed.mp4"
    shutil.copyfile(INPUT_VIDEO, preprocessed)

    # 3. audio extraction (extract before stripping, so reference is silent)
    audio = extract_audio(preprocessed, f"{WORK}/audio.aac")
    print("audio:", audio)

    # 4. strip audio from working video → silent reference for Seedream/Seedance
    silent_video = f"{WORK}/preprocessed_silent.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", preprocessed,
        "-an", "-c:v", "copy", silent_video,
    ], check=True, capture_output=True)
    print("silent video:", silent_video)

    # 5. anchor keyframes (A0-A4 at 0%, 25%, 50%, 75%, 100%) — from SILENT video
    ts = anchor_timestamps(info["duration"])
    raws = [extract_frame(silent_video, t, f"{WORK}/kf{i}_raw.jpg")
            for i, t in enumerate(ts, 0)]
    print("raw keyframes:", raws)

    # 6. QC Gate: Seedream round-0 → Evaluator → Reviser loop → QC-passed KFs
    #    Uses the SILENT video as input (no audio in the pipeline)
    kfs_json = os.path.join(WORK, "qc_keyframes.json")
    qc_report = os.path.join(WORK, "qc_report.json")
    anchor_ts = ",".join(str(t) for t in ts)

    print("=== QC Gate (silent video) ===")
    result = subprocess.run(
        [sys.executable, "qc_gate.py",
         "--raw-video", silent_video,
         "--style-image", STYLE_REF,
         "--anchor-timestamps", anchor_ts,
         "--output-kfs-json", kfs_json,
         "--report-path", qc_report],
        capture_output=True, text=True, timeout=1800,
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

    # Extract URLs: all interior anchors (A1-A4) passed to Seedance
    kf_urls = [kf["image_url"] for kf in kf_data["keyframes"]
               if kf["anchor_id"] != "A0"]
    print("QC-passed keyframe URLs:", kf_urls)

    # 7. Seedance 2.0 — 720p, silent reference → silent output
    #    VLM gate loops: up to 3 attempts (1 initial + 2 retries)
    from pipeline.vlm_gate import grade_video
    from pipeline.prompts import SEEDANCE_PROMPT

    max_vlm_attempts = 3
    vlm_attempt = 1
    vlm_passed = False
    vlm_results = []
    seedance_seed = 42

    while vlm_attempt <= max_vlm_attempts:
        print(f"\n=== Seedance generation attempt {vlm_attempt}/{max_vlm_attempts} ===")
        task_id = submit_task(STYLE_REF_URI, kf_urls, INPUT_VIDEO_URI,
                              duration=round(info["duration"]), seed=seedance_seed)
        print("Seedance task:", task_id)
        stylized_url = wait_for_task(task_id)
        silent = download(stylized_url, f"{WORK}/stylized_silent.mp4")
        print("stylized silent:", silent)

        # VLM gate
        print(f"\n=== VLM Gate (attempt {vlm_attempt}) ===")
        ts = anchor_timestamps(info["duration"])
        vlm_result = grade_video(os.environ["ARK_API_KEY"], silent, ts,
                                   SEEDANCE_PROMPT, attempt=vlm_attempt)
        vlm_results.append(vlm_result)
        print(f"  pass: {vlm_result['pass']}")
        print(f"  failures: {vlm_result['failures']}")
        for dim, s in vlm_result.get('scores', {}).items():
            print(f"    {dim}: {s['score']} — {s['rationale'][:100]}")

        if vlm_result['pass']:
            vlm_passed = True
            print("  ✅ VLM gate passed")
            break

        vlm_attempt += 1
        seedance_seed += 10  # change seed for retry
        if vlm_attempt <= max_vlm_attempts:
            print(f"  Retrying with seed={seedance_seed}...")

    if not vlm_passed:
        print(f"\n⚠️  VLM gate failed after {max_vlm_attempts} attempts. Using best result.")

    # 8. audio mux → final deliverable (re-mux the pre-extracted audio)
    final = mux_audio(silent, audio, f"{WORK}/unity_handdrawn_final.mp4")
    print("DONE →", final)
    print("final probe:", probe(final))


if __name__ == "__main__":
    main()
