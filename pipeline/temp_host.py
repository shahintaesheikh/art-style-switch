"""Module 0 — temp-host upload helper (plan §4)."""

import subprocess

import requests


def tmp_upload(local: str, name: str) -> str:
    """Upload to transfer.sh. Swap the body if you use a different temp host or a tunnel."""
    url = subprocess.check_output(
        ["curl", "-sS", "--upload-file", local, f"https://transfer.sh/{name}"]
    ).decode().strip()
    if not url.startswith("http"):
        raise RuntimeError(f"temp upload failed: {url}")
    # verify HEAD
    r = requests.head(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return url