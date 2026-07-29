#!/usr/bin/env python3
"""Unity hand-drawn style pipeline — end-to-end runner (plan §15).

Hosting model: local files are processed locally (ffmpeg / base64); the two
inputs Seedance needs as references live in the ModelArk Asset Library
(uploaded once via console) and are referenced by asset:// URIs.
"""

import os
import shutil

import requests
from dotenv import load_dotenv

load_dotenv()

import pipeline  # noqa: F401 — truststore injection (corporate TLS interception)
from pipeline.audio import extract_audio
from pipeline.keyframes import anchor_timestamps, extract_frame
from pipeline.mux import mux_audio
from pipeline.probe import probe
from pipeline.seedance import submit_task, wait_for_task
from pipeline.seedream import gen_styled_keyframes

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

    # 3. audio extraction
    audio = extract_audio(preprocessed, f"{WORK}/audio.aac")
    print("audio:", audio)

    # 5. anchor keyframes (A1/A2; A0 is the user-provided style ref)
    raws = [extract_frame(preprocessed, ts, f"{WORK}/kf{i}_raw.jpg")
            for i, ts in enumerate(anchor_timestamps(info["duration"]), 1)]
    print("raw keyframes:", raws)

    # 6. Seedream i2i styled anchors (base64 inline; ModelArk-hosted URLs back)
    kf_urls = gen_styled_keyframes(raws, STYLE_REF)
    print("styled keyframe URLs:", kf_urls)

    # 7. Seedance 2.0 — inputs referenced as asset:// URIs (no hosting needed)
    task_id = submit_task(STYLE_REF_URI, kf_urls, INPUT_VIDEO_URI,
                          duration=round(info["duration"]))
    print("Seedance task:", task_id)
    stylized_url = wait_for_task(task_id)
    silent = download(stylized_url, f"{WORK}/stylized_silent.mp4")
    print("stylized silent:", silent)

    # 8. audio mux → final deliverable
    final = mux_audio(silent, audio, f"{WORK}/unity_handdrawn_final.mp4")
    print("DONE →", final)
    print("final probe:", probe(final))


if __name__ == "__main__":
    main()
