# Unity Hand-Drawn Style Pipeline — Zero-Infra Variant

Re-renders a Unity gameplay clip (`unity-5.mp4`) in a consistent pencil +
colored-pencil sketch style (defined by `handDrawnStyle.jpeg`) using
BytePlus ModelArk (Seedream i2i + Seedance 2.0).

**Zero-infra:** no VOD, no TOS, no S3, no object storage. Images go as
base64 inline; the input video is dropped on a temp file host with one
`curl`; all media processing is local `ffmpeg`. The only external
services are BytePlus ModelArk and a free temp file host.

Full spec: `.pi/plan.md`.

## Layout

```
run_pipeline.py        # end-to-end runner (plan §15)
pipeline/
  temp_host.py         # Module 0 — temp file hosting via cloudflared tunnel (§4)
  probe.py             # Module 1 — ffprobe input (§5)
  preprocess.py        # Module 2 — optional resize/transcode (§6)
  audio.py             # Module 3 — audio extraction (§7)
  scenes.py            # Module 4 — optional scene segmentation (§8)
  keyframes.py         # Module 5 — anchor keyframe extraction (§9)
  seedream.py          # Module 6 — styled keyframes via Seedream i2i (§10)
  seedance.py          # Module 7 — stylized video via Seedance 2.0 (§11)
  mux.py               # Module 8 — audio mux (§12)
  prompts.py           # Seedream + Seedance prompts (§10.1, §11.1)
```

## Prerequisites

- `ffmpeg` / `ffprobe` on PATH
- `cloudflared` (`brew install cloudflared`) — quick tunnel for temp file hosting
- Python 3.10+, `pip install -r requirements.txt`
- BytePlus ModelArk API key (see plan §2.2)

## Setup

```bash
cp .env.example .env   # fill in ARK_API_KEY
# drop unity-5.mp4 and handDrawnStyle.jpeg in the project root
python run_pipeline.py
```

Output: `work/unity_handdrawn_final.mp4`.
