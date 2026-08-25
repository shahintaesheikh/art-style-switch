#!/usr/bin/env python3
"""Re-run the pipeline from QC Gate onward using cached LLM Gate prompts
from the monochromatic run. Skips the VLM/SerpAPI step entirely."""

import json
import os
import re
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

from pipeline.audio import extract_audio
from pipeline.keyframes import anchor_timestamps, extract_frame
from pipeline.mux import mux_audio
from pipeline.probe import probe
from pipeline.seedance import submit_task, wait_for_task

WORK = os.environ.get("WORK_DIR", "./work")
INPUT_VIDEO = os.environ["INPUT_VIDEO"]
STYLE_REF = os.environ["STYLE_REF"]
INPUT_VIDEO_URI = os.environ["INPUT_VIDEO_URI"]

CACHED_LOG = "work/prompts_log/prompt_llm_gate_20260819_142608_905176.txt"


def download(url: str, out: str) -> str:
    import requests
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return out


def main() -> None:
    os.makedirs(WORK, exist_ok=True)

    # Remove the unwanted reference
    unwanted = os.path.join(WORK, "extra_reference", "mono_serpapi_ref_01.jpg")
    if os.path.exists(unwanted):
        os.remove(unwanted)
        print(f"Removed: {unwanted}")
    else:
        print(f"(already removed: {unwanted})")

    # Read cached prompts from the last LLM Gate run
    log = open(CACHED_LOG).read()
    m_kf = re.search(r"## keyframe_prompt.*?\n(.*?)\n\n## seedance_prompt", log, re.DOTALL)
    m_sd = re.search(r"## seedance_prompt.*?\n(.*?)$", log, re.DOTALL)
    KEYFRAME_PROMPT = m_kf.group(1).strip() if m_kf else ""
    SEEDANCE_PROMPT = m_sd.group(1).strip() if m_sd else ""
    print(f"Cached prompts loaded from {CACHED_LOG}")
    print(f"  keyframe: {len(KEYFRAME_PROMPT)} chars")
    print(f"  seedance: {len(SEEDANCE_PROMPT)} chars")

    # Probe video
    info = probe(INPUT_VIDEO)
    print("probe:", info)

    # Audio extraction
    audio = extract_audio(INPUT_VIDEO, f"{WORK}/audio.aac")
    print("audio:", audio)

    # Strip audio
    silent_video = f"{WORK}/preprocessed_silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", INPUT_VIDEO, "-an", "-c:v", "copy", silent_video],
                   check=True, capture_output=True)
    print("silent video:", silent_video)

    # Anchor keyframes
    ts = anchor_timestamps(info["duration"])
    raws = [extract_frame(silent_video, t, f"{WORK}/kf{i}mono.jpg")
            for i, t in enumerate(ts, 0)]
    print("raw keyframes:", raws)

    # QC Gate (fresh — no cache)
    kfs_json = os.path.join(WORK, "qc_keyframes.json")
    qc_report = os.path.join(WORK, "qc_report.json")
    anchor_ts = ",".join(str(t) for t in ts)

    # Remove any cached QC artifacts from previous failed run
    for f in [kfs_json, qc_report]:
        if os.path.exists(f):
            os.remove(f)

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
    kf_urls = [kf["image_url"] for kf in kf_data["keyframes"]
               if kf["anchor_id"] != "A0"]
    print("QC-passed keyframe URLs:", kf_urls)

    # Seedance
    print("\n=== Seedance generation ===")
    task_id = submit_task(STYLE_REF, kf_urls, INPUT_VIDEO_URI,
                          duration=round(info["duration"]), seed=42,
                          prompt=SEEDANCE_PROMPT)
    print("Seedance task:", task_id)
    stylized_url = wait_for_task(task_id)
    silent = download(stylized_url, f"{WORK}/stylized_silent.mp4")
    print("stylized silent:", silent)

    # Audio mux
    final = mux_audio(silent, audio, f"{WORK}/unity_monochromatic_final.mp4")
    print("DONE →", final)
    print("final probe:", probe(final))


if __name__ == "__main__":
    main()