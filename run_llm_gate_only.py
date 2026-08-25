#!/usr/bin/env python3
"""Run only the LLM Gate (VLM analysis + SerpAPI enrichment), then
download the web style references into work/extra_reference/."""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

from pipeline.llm_gate import generate_style_prompts

WORK = os.environ.get("WORK_DIR", "./work")
STYLE_REF = os.environ.get("STYLE_REF", "")
API_KEY = os.environ.get("ARK_API_KEY", "")

if not STYLE_REF or not API_KEY:
    print("ERROR: STYLE_REF and ARK_API_KEY must be set in .env")
    sys.exit(1)


def download_reference(url: str, dest_dir: Path, index: int) -> str:
    """Download a single web reference image and return the local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    fname = f"serpapi_ref_{index:02d}{ext}"
    path = dest_dir / fname
    print(f"  downloading [{index}]: {url[:80]}...")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    size_kb = os.path.getsize(path) / 1024
    print(f"    → {path} ({size_kb:.0f} KB)")
    return str(path)


def main() -> None:
    extra_dir = Path(WORK) / "extra_reference"
    extra_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== LLM Gate (standalone) ===")
    print(f"  style_ref: {STYLE_REF}")
    print(f"  extra_dir: {extra_dir}")

    style_prompts = generate_style_prompts(API_KEY, STYLE_REF)

    analysis = style_prompts["style_analysis"]
    print(f"\n  style_label: {analysis.get('style_label', 'unknown')}")
    print(f"  medium: {analysis.get('medium', '')[:80]}...")
    print(f"  keyframe prompt: {len(style_prompts['keyframe'])} chars")
    print(f"  seedance prompt: {len(style_prompts['seedance'])} chars")

    web_refs = analysis.get("web_style_references", [])
    print(f"\n  web_style_references count: {len(web_refs)}")

    if web_refs:
        print(f"\n  Downloading to {extra_dir}/ ...")
        downloaded = []
        for i, url in enumerate(web_refs):
            try:
                local = download_reference(url, extra_dir, i)
                downloaded.append(local)
            except Exception as exc:
                print(f"    [SKIP] download failed: {exc}")

        print(f"\n  Downloaded {len(downloaded)}/{len(web_refs)} references")
        print(f"  Directory: {extra_dir.resolve()}")
        for p in sorted(extra_dir.iterdir()):
            print(f"    {p.name}  ({os.path.getsize(p)/1024:.0f} KB)")
    else:
        print("  (no web references — SerpAPI key missing or enrichment skipped)")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()