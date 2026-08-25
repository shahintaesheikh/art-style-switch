# Art-Style-Switch Pipeline

Re-renders a gameplay clip in a certain style using
BytePlus ModelArk (Seedream i2i + Seedance 2.5 + Managed Agents (Seed 2.1)).

**Zero-infra:** This demo specifically has no VOD, no TOS, no S3, no object storage. Images go to
Seedream as base64 inline; the input video and style reference are
uploaded once to the **ModelArk Asset Library** (console upload) and
referenced by `asset://` URIs; all media processing is local `ffmpeg`.
The only external service is BytePlus ModelArk.


## Prerequisites

- `ffmpeg` / `ffprobe` on PATH
- Python 3.10+, `pip install -r requirements.txt`
- BytePlus ModelArk API key (see plan §2.2)

## Setup

```bash
cp .env.example    # fill in ARK_API_KEY, local input paths, and asset:// URIs (or publicly exposed image URLs or base64 using image utils)
Ability to use ffmpeg and ffprobe (on PATH)
Python 3.10+, run 'pip install -r requirements.txt'
python run_pipeline.py
```

Output: `work/unity_handdrawn_final.mp4`.
