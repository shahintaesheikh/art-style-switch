# Unity Hand\-Drawn Style Pipeline — Zero\-Infra Coding\-Agent Variant

> **Audience:** a coding agent that will run this pipeline exactly once \(or a handful of times\) to prove the style transfer works on `unity-5.mp4`, then demo the result to Unity\. No production deployment, no multi\-tenant service, no media cloud — just a shell on any machine with `ffmpeg` and Python\.
> **Goal:** re\-render a Unity gameplay clip in a consistent pencil \+ colored\-pencil sketch style \(defined by `handDrawnStyle.jpeg`\) across the entire generated video\.
> **Variant:** zero\-infra\. No VOD, no TOS, no S3, no R2, no object\-storage account of any kind\. Images are sent as base64 inline; the single input video is dropped on a temp file host with one `curl`; Seedream output URLs are passed straight through to Seedance without re\-hosting\. All media processing is **local ffmpeg**\. The only external services you call are **BytePlus ModelArk** \(generation\) and a free temp file host \(one 8 MB upload\)\.
> 
> 

For production deployment at scale, swap the local ffmpeg \+ temp\-host steps for BytePlus VOD\. Section 18 explains exactly what VOD buys you and when the switch is justified\. Nothing in this doc changes the sibling BytePlus Edition doc\.

---

## 1\. Why zero\-infra for a one\-off demo

BytePlus ModelArk's only hard input contract is: **every ****`image_url`**** / ****`video_url`**** must return bytes when ModelArk's servers GET it**[\[Video generation\]](https://docs.byteplus.com/en/docs/modelark/2371066)\. It does not care whether those bytes come from VOD, TOS, S3, or a random public URL on the internet\. For a one\-demo\-to\-Unity we can collapse the entire "object storage" layer as follows:

- **Small images** \(style reference, raw keyframes\) → send as **base64 data URIs** directly in the Seedream JSON body\. JPEGs are 100–300 KB each; base64 overhead is \~33%, well within every request size limit\.

- **The 8 MB input video** → upload once to a free temp file host \(`transfer.sh` or similar\) with a single `curl`\. The URL only needs to stay up long enough for ModelArk to fetch it \(a few minutes after you submit the Seedance job\)\.

- **ModelArk outputs** \(Seedream styled keyframes, Seedance final video\) come back as ModelArk\-hosted HTTPS URLs\. Pass those URLs directly to the next step — no re\-upload needed\.

- **All media processing** \(probe, audio extract, keyframe extraction, final audio mux\) runs locally with `ffmpeg`\. No cloud workers, no queue, no signing\.

Expected end\-to\-end wall time: **4–8 minutes**\.

---

## 2\. Prerequisites

### 2\.1 Local tools

- **ffmpeg** and **ffprobe** on PATH \(any 5\.x/6\.x build\)\.

- **Python 3\.10\+** with `pip install requests python-dotenv`\.

    - You do NOT need `boto3`, `awscli`, or any cloud\-storage SDK\.

    - You do NOT need the BytePlus VOD SDK\.

    - You do NOT need `scenedetect` for the 8 s demo clip \(it has no cuts\); install it only if you test on longer clips\.

- **curl** with `--upload-file` support \(ships with every OS\)\.

- \(Optional but helpful\) `cloudflared` or `ngrok` if your corporate firewall blocks outbound uploads to `transfer.sh` — §4 shows an alternate "serve locally \+ tunnel" path\.

### 2\.2 BytePlus ModelArk

1. Activate ModelArk in the BytePlus console and create an API key at [API Key management](https://console.byteplus.com/ark/region:ark ap-southeast-1/apikey)\.

2. Enable these models in ModelArk \(Model IDs current as of 2026; verify in the console\):

    - **Video:**`dreamina-seedance-2-0-260128` \(Seedance 2\.0\) — supports 1 video \+ up to 9 reference images, durations 4–15 s, 3:4 portrait\.

    - **Image \(i2i\):**`doubao-seedream-5-0-lite-260128` \(Seedream\) — supports image\-to\-image and multiple reference images\.

    - **Do NOT use ****`dola-seedream-5-0-pro-260628`**** for i2i** — it does not accept the `image` parameter and will return an error\.

3. ModelArk endpoint \(ap\-southeast\-1\): `https://ark.ap-southeast.bytepluses.com/api/v3`\.

### 2\.3 Environment variables

Create a `.env`:

```bash
# ---- BytePlus ModelArk ----
export ARK_API_KEY="<your-modelark-api-key>"
export ARK_BASE_URL="https://ark.ap-southeast.bytepluses.com/api/v3"

# ---- Inputs (place these in the project root) ----
export INPUT_VIDEO="./unity-5.mp4"
export STYLE_REF="./handDrawnStyle.jpeg"

# ---- Working directory (created for you) ----
export WORK_DIR="./work"
mkdir -p "$WORK_DIR"
```

### 2\.4 Input facts for the demo clip \(verified\)

`unity-5.mp4` — 560×752, 8\.04 s, 24 fps, H\.264/AAC MP4, continuous forward\-dolly shot, red player car centered, oncoming blue NPC cars, low\-poly city, no cuts, no humans\.

`handDrawnStyle.jpeg` — pencil \+ colored\-pencil sketch of frame 0 \(same composition, graphite hatching on buildings/road, crimson colored\-pencil car, light\-blue sky wash\)\.

**Critical shortcut:** 560×752 matches the Seedance 2\.0 **3:4 @ 480p** bucket exactly \(see §9 for the resolution table\)\. No transcode/resize is required\.

---

## 3\. Pipeline at a glance

```
[1] Probe input (ffprobe) ─► resolution, duration, fps
[2] Extract audio (ffmpeg) ─► work/audio.aac
[3] (if D > 15 s) Scene-split (PySceneDetect) ─► segments.json         [SKIP for 8 s demo]
[4] Extract 2 raw keyframes at ~33% / ~66% (ffmpeg) ─► kf1_raw.jpg, kf2_raw.jpg
[5] Upload ONLY the input video to a temp host ─► VIDEO_URL             (one curl)
[6] Seedream i2i (parallel for A1, A2)  images as base64  ─► KF1_STYLED_URL, KF2_STYLED_URL
        (returned ModelArk-hosted URLs, no re-upload)
[7] Seedance 2.0
       reference_video = VIDEO_URL (temp-host URL)
       reference_image[0] = STYLE_REF uploaded to temp host (~100 KB)
       reference_image[1] = KF1_STYLED_URL (from Seedream, ModelArk-hosted)
       reference_image[2] = KF2_STYLED_URL (from Seedream, ModelArk-hosted)
    ─► STYLIZED_VIDEO_URL (ModelArk-hosted)
[8] Download stylized video + mux audio (ffmpeg) ─► unity_handdrawn_final.mp4
[9] QC + deliver
```

Total network calls beyond ModelArk: **2 ****`curl --upload-file`**** calls** \(style ref jpeg \+ input video\) to a temp host\. All other bytes travel in JSON bodies \(base64\) or come back from ModelArk already hosted\.

---

## 4\. Module 0 — The temp\-host helper

We use `transfer.sh`, a free file\-hosting service that returns a public URL from a single `curl --upload-file`\. No account, no signup\. URLs live for \~14 days, which is far longer than we need \(ModelArk fetches inputs within minutes of job submission\)\.

```bash
# Upload a file and echo its public URL.
tmp_upload() {
  # $1 = local path, $2 = remote filename (used in the returned URL)
  curl -sS --upload-file "$1" "https://transfer.sh/$2"
}
```

If `transfer.sh` is blocked on your network, use one of:

- **`0x0.st`** — same one\-liner: `curl -sS -F "file=@$1" https://0x0.st`

- **`tmpfiles.org`** — `curl -sS -F "file=@$1" https://tmpfiles.org/api/v1/upload | jq -r .data.url`

- **Local serve \+ tunnel \(zero upload\)** — if your file can't leave your network:

    ```bash
    # terminal 1
    cd "$(dirname "$INPUT_VIDEO")" && python3 -m http.server 8000
    # terminal 2
    cloudflared tunnel --url http://localhost:8000
    # → https://<random>.trycloudflare.com/unity-5.mp4  (and /handDrawnStyle.jpeg)
    ```

In the Python runner we wrap this as a single function \(§12\)\.

---

## 5\. Module 1 — Probe

```python
import json, subprocess

def probe(path: str) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration",
        "-of", "json", path,
    ])
    d = json.loads(out)
    s = d["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {
        "width": s["width"],
        "height": s["height"],
        "fps": float(num) / float(den),
        "duration": float(s.get("duration") or d["format"]["duration"]),
    }

INFO = probe("unity-5.mp4")
# → {"width": 560, "height": 752, "fps": 24.0, "duration": 8.04}
```

Confirm `width/height ≈ 3/4` and that the resolution matches a bucket in §9\. For the demo clip, 560×752 = native 3:4@480p → no resize\.

---

## 6\. Module 2 — \(Optional\) Resize / transcode

For the demo clip, skip:

```bash
cp "$INPUT_VIDEO" "$WORK_DIR/preprocessed.mp4"
```

If you need a different bucket \(e\.g\., upscale to 720p 3:4 = 834×1112\):

```bash
ffmpeg -y -i "$INPUT_VIDEO" \
  -vf "scale=834:1112:flags=lanczos" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 192k \
  "$WORK_DIR/preprocessed.mp4"
```

Always set `-pix_fmt yuv420p` — ModelArk rejects exotic pixel formats\.

---

## 7\. Module 3 — Audio extraction

```bash
ffmpeg -y -i "$WORK_DIR/preprocessed.mp4" -vn -c:a copy "$WORK_DIR/audio.aac"
```

- `-c:a copy` stream\-copies if the source is already AAC; use `-c:a aac -b:a 192k` to force re\-encode\.

- If `ffprobe` shows no audio stream, skip this module and skip mux in Module 8\.

---

## 8\. Module 4 — \(Optional\) Scene segmentation

For clips **≤15 s with no cuts \(like the demo\)**, skip entirely — one segment\.

For longer clips or clips with cuts, split into Seedance\-sized 5–10 s segments:

```bash
pip install scenedetect[opencv]   # one-time
scenedetect --input "$WORK_DIR/preprocessed.mp4" \
  detect-content list-scenes \
  -s "$WORK_DIR/segments.stats.csv" -o "$WORK_DIR"
```

Parse the `-Scenes.csv` output into `{start, end}` segments and run Modules 5–7 per segment in parallel\. Concat in Module 8\. For your 8 s Unity demo this stays off\.

---

## 9\. Module 5 — Extract anchor keyframes

Three anchors:

|Anchor|Timestamp|Source|
|---|---|---|
|A0|0\.0 s|User\-provided `handDrawnStyle.jpeg` \(already styled\)|
|A1|`floor(D * 0.33)` s|Raw frame from preprocessed video|
|A2|`floor(D * 0.66)` s|Raw frame from preprocessed video|

For the 8\.04 s demo: **0 s / 2 s / 5 s**\.

```bash
A1_TS=2
A2_TS=5
ffmpeg -y -ss "$A1_TS" -i "$WORK_DIR/preprocessed.mp4" -frames:v 1 -q:v 2 "$WORK_DIR/kf1_raw.jpg"
ffmpeg -y -ss "$A2_TS" -i "$WORK_DIR/preprocessed.mp4" -frames:v 1 -q:v 2 "$WORK_DIR/kf2_raw.jpg"
```

### 9\.1 Upload ONLY the two things Seedance needs as URLs

Seedream accepts images as base64, but Seedance's `reference_image` entries are URLs\. We need the style ref and the input video to be at a public HTTPS URL\. \(The other two reference images — KF1\_STYLED and KF2\_STYLED — will come back from Seedream as ModelArk\-hosted URLs, so we pass those through without uploading anything\.\)

```bash
STYLE_REF_URL=$(tmp_upload "$STYLE_REF" "handDrawnStyle.jpeg")
VIDEO_URL=$(tmp_upload "$WORK_DIR/preprocessed.mp4" "preprocessed.mp4")
export STYLE_REF_URL VIDEO_URL
echo "STYLE_REF_URL=$STYLE_REF_URL"
echo "VIDEO_URL=$VIDEO_URL"
```

Smoke\-test both:

```bash
curl -sSI "$STYLE_REF_URL" | head -1    # expect HTTP/2 200
curl -sSI "$VIDEO_URL"    | head -1    # expect HTTP/2 200
```

\(If you use the `cloudflared` path, skip `tmp_upload` and set the URLs directly to your tunnel URLs\.\)

---

## 10\. Module 6 — Styled keyframe generation \(Seedream i2i, base64 in\)

Generates the two extra interior anchors \(A1, A2\) so Seedance has style checkpoints in the middle of the clip\. Run both calls **in parallel**\.

Images are sent as **base64 data URIs** inline in the JSON body → no upload, no temp\-hosting for raw frames, no storage account\.

### 10\.1 Prompt \(English, identical for A1 and A2\)

```
Redraw the provided gameplay frame in the exact hand-drawn sketch style of the reference image.

Style requirements (match the reference image exactly):
- Medium: pencil and colored-pencil sketch on off-white paper; visible paper grain.
- Line work: fine graphite hatching and cross-hatching for all shading; sketchy, slightly irregular outlines as if drawn by hand with a soft pencil; no clean vector lines, no cel-shading, no 3D-rendered look.
- Color: the player's red car must be filled with red colored-pencil strokes (same crimson tone as the reference, hatched, not flat fill); buildings are drawn with graphite hatching over a pale cream/white base with windows picked out in small colored-pencil squares (yellows, light blues, pinks, greys) matching the reference; sky is a light-blue colored-pencil wash; road is graphite hatching with radial speed/motion strokes converging toward the vanishing point; distant blue cars are blue colored-pencil with the same hatching treatment.
- Shading: all shadows and gradients produced by pencil stroke density, not by smooth gradients or lighting models.
- Texture: visible pencil stroke direction; slight paper-tooth texture under the color; subtle hand-made imperfection (slightly wobbly lines, minor stroke overflow).

Content preservation (must match the raw source frame exactly — do not invent, remove, or move anything):
- Preserve camera perspective, focal length, and road vanishing point exactly.
- Preserve the position, scale, orientation, and count of every object: the player red car (same red), all oncoming blue cars, all buildings on both sides, road width, sky area.
- Preserve the composition and framing 1:1; do not zoom, crop, rotate, or letterbox.
- Preserve the approximate colors of the source (red = player car, blue = NPC cars, light blue = sky, greys/creams = road and buildings), only reinterpreted through pencil/colored-pencil.

Forbidden: photorealism, 3D renders, smooth gradients, airbrushed look, anime cel shading, cartoon outlines with flat fills, watercolor blobs without pencil lines, photography, AI-overprocessed smooth textures, extra lens flare, extra motion blur not in the source, any signature, watermark, or text.

Output: 3:4 portrait matching the input aspect ratio, no watermark, no border, no text, no signature.
```

### 10\.2 Seedream request \(run twice, parallel, base64 payloads\)

```python
import os, base64, requests
from concurrent.futures import ThreadPoolExecutor

ARK_BASE = os.environ["ARK_BASE_URL"]
ARK_KEY  = os.environ["ARK_API_KEY"]
WORK     = os.environ["WORK_DIR"]
KEYFRAME_PROMPT = """<§10.1 prompt, verbatim>"""

def b64_image(path: str) -> str:
    """Return a data URI suitable for ModelArk's image[] parameter."""
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode("ascii")
    # ModelArk accepts both raw base64 strings and data URIs. Raw base64 is fine.
    return b

def gen_styled_keyframe(raw_path: str, style_path: str, seed: int):
    payload = {
        "model": "doubao-seedream-5-0-lite-260128",
        "prompt": KEYFRAME_PROMPT,
        "image": [b64_image(raw_path), b64_image(style_path)],
        "size": "2K",
        "response_format": "url",
        "watermark": False,
        "seed": seed,
    }
    r = requests.post(
        f"{ARK_BASE}/images/generations",
        headers={"Authorization": f"Bearer {ARK_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=120,
    )
    r.raise_for_status()
    # Seedream returns a short-lived ModelArk-hosted URL. We don't need to download
    # or re-upload it — Seedance can read it directly as a reference_image.
    return r.json()["data"][0]["url"]

with ThreadPoolExecutor(max_workers=2) as ex:
    f1 = ex.submit(gen_styled_keyframe, f"{WORK}/kf1_raw.jpg", os.environ["STYLE_REF"], 42)
    f2 = ex.submit(gen_styled_keyframe, f"{WORK}/kf2_raw.jpg", os.environ["STYLE_REF"], 43)
    KF1_STYLED_URL = f1.result()
    KF2_STYLED_URL = f2.result()

print("KF1_STYLED_URL =", KF1_STYLED_URL)
print("KF2_STYLED_URL =", KF2_STYLED_URL)
```

### 10\.3 Keyframe QC

Before moving on, optionally download the two styled keyframes and spot\-check:

```python
def dl(url, path):
    open(path, "wb").write(requests.get(url, timeout=60).content)
dl(KF1_STYLED_URL, f"{WORK}/kf1_styled.jpg")
dl(KF2_STYLED_URL, f"{WORK}/kf2_styled.jpg")
```

- Red player car red and centered; blue NPC cars blue and present\.

- Road hatching converges to vanishing point\.

- No 3D remnant geometry\.

- If a keyframe fails, regenerate with a new `seed` or append a strengthener \("heavier pencil hatching, more visible cross\-hatching on buildings"\)\.

---

## 11\. Module 7 — Stylized video generation \(Seedance 2\.0\)

### 11\.1 Prompt \(English\)

```
【Global setup】Output portrait 3:4. The reference_video provides camera, motion, composition, timing, object positions, occlusion, car movement, road perspective, and all editing structure ONLY — do NOT carry over any of its 3D-rendered look, flat shading, smooth gradients, or game-engine lighting. The three reference_images define the visual style and color treatment for the entire clip — every frame of the output must look like it was hand-drawn in that exact pencil + colored-pencil sketch style.

【Per-frame style rules】Every single output frame must be fully redrawn in the hand-drawn sketch style of the reference images:
- Medium: pencil and colored-pencil on off-white paper; subtle paper grain visible throughout.
- Line work: soft graphite outlines, slightly wobbly/sketchy (not perfectly straight), with hatching and cross-hatching for all shading; no clean vector lines, no cel-shading.
- Color: player's car remains red colored-pencil (same crimson tone as the reference images, hatched, not flat); oncoming NPC cars remain blue colored-pencil; buildings are graphite on cream with small colored-pencil window squares (yellows, light blues, pinks, greys) distributed as in the reference; sky is a light-blue colored-pencil wash; road is graphite hatching with radial speed strokes converging to the vanishing point that intensify with forward motion to convey speed.
- All shadows and gradients are produced by pencil stroke density, not by smooth shading.
- The stroke hatching on the road should animate coherently with camera motion — it must not flicker or jitter between frames.

【Style consistency anchors】
- @Image 1 (frame 0 — handDrawnStyle reference) anchors the opening style.
- @Image 2 (frame at ~33% anchor) and @Image 3 (frame at ~66% anchor) anchor the mid-clip style as new cars enter and the perspective deepens.
Interpolate the style smoothly between these anchors; style, stroke weight, paper color, and palette must remain perceptually identical from the first frame to the last — no drift toward 3D, photorealism, or a different art style at any point.

【Content preservation — strict】
- Preserve the camera dolly / forward motion exactly as in reference_video; preserve the road vanishing point, building placement on both sides, the player red car's position (centered lower-frame) and speed, every oncoming blue car's timing of entry, position, scale, and count.
- Preserve the exact colors of objects: player car red, NPC cars blue, sky light blue, buildings cream/grey with colored window panes.
- Do NOT add people, animals, signs, UI elements, lens flares, speed lines that weren't in the source, or any extra objects. Do NOT remove or relocate any object present in the source.
- Do NOT change the pacing, timing, or action.

【Forbidden】Photorealism, 3D-rendered look, smooth gradients, airbrushed shading, anime cel shading, cartoon outlines with flat fills, watercolor-only without pencil underdrawing, photography, AI over-smoothed textures, flicker between frames, jittery outlines, morphing objects, warping buildings, tire/shape deformation on the cars, any signature, watermark, text, or on-screen UI.
```

### 11\.2 Submit task

```python
SEEDANCE_PROMPT = """<§11.1 prompt, verbatim>"""

r = requests.post(
    f"{ARK_BASE}/contents/generations/tasks",
    headers={"Authorization": f"Bearer {ARK_KEY}", "Content-Type": "application/json"},
    json={
        "model": "dreamina-seedance-2-0-260128",
        "content": [
            {"type": "text",      "text": SEEDANCE_PROMPT},
            {"type": "image_url", "image_url": {"url": os.environ["STYLE_REF_URL"]}, "role": "reference_image"},
            {"type": "image_url", "image_url": {"url": KF1_STYLED_URL},              "role": "reference_image"},
            {"type": "image_url", "image_url": {"url": KF2_STYLED_URL},              "role": "reference_image"},
            {"type": "video_url", "video_url": {"url": os.environ["VIDEO_URL"]},     "role": "reference_video"},
        ],
        "ratio": "3:4",
        "duration": 8,
        "seed": 42,
        "camera_fixed": False,
        "watermark": False,
    },
    timeout=60,
)
r.raise_for_status()
task_id = r.json()["id"]
print("Seedance task:", task_id)
```

`camera_fixed` and `watermark` are top\-level JSON booleans — do **not** put them in the prompt text\.

### 11\.3 Poll until complete

```python
import time
while True:
    g = requests.get(
        f"{ARK_BASE}/contents/generations/tasks/{task_id}",
        headers={"Authorization": f"Bearer {ARK_KEY}"}, timeout=30,
    )
    g.raise_for_status()
    d = g.json()
    print("Seedance status:", d.get("status"))
    if d.get("status") == "succeeded":
        STYLIZED_VIDEO_URL = d["content"]["video_url"]
        break
    if d.get("status") in ("failed", "cancelled"):
        raise SystemExit(f"Seedance failed: {d}")
    time.sleep(10)

# Download immediately — URL is ModelArk-hosted and short-lived (~24 h)
subprocess.check_call(["curl", "-sSL", "-o", f"{WORK}/stylized_silent.mp4", STYLIZED_VIDEO_URL])
```

### 11\.4 Seedance QC checklist

- **Frame\-0 match:** first frame is visually indistinguishable in composition and style from `handDrawnStyle.jpeg`\.

- **No 3D remnants:** spot\-check at 2 s, 5 s, 7 s — buildings and cars fully sketched, not sketch\-on\-3D\.

- **Object integrity:** same count of blue cars enter at the same times; red car stays centered; no morphing\.

- **Temporal coherence:** no flicker in building lines or hatching direction; road hatching moves smoothly with forward motion\.

- **End\-of\-clip style lock:** the last second still reads as pencil\-sketch\.

If QC fails: retry with a different `seed`, strengthen the Forbidden section, or add a 4th anchor at \~90% \(repeat Module 6 for t=7 and add it as a 4th `reference_image` with "@Image 4 anchors the closing shot\."\)\.

---

## 12\. Module 8 — Audio mux \(ffmpeg\)

Single\-segment case \(demo clip\):

```bash
ffmpeg -y \
  -i "$WORK_DIR/stylized_silent.mp4" \
  -i "$WORK_DIR/audio.aac" \
  -c:v copy -c:a aac -b:a 192k \
  -map 0:v:0 -map 1:a:0 \
  -shortest -movflags +faststart \
  "$WORK_DIR/unity_handdrawn_final.mp4"
```

- `-c:v copy` stream\-copies the Seedance H\.264 \(no re\-encode, instant\)\.

- `-c:a aac` normalizes audio to AAC \(use `-c:a copy` if you know the extracted track is already AAC\)\.

- `-shortest` trims whichever stream is longer\.

- `-movflags +faststart` makes the MP4 web\-streamable\.

### Multi\-segment concat \(only if Module 4 ran\)

If you split into N segments, concatenate the silent outputs first then mux audio:

```bash
: > "$WORK_DIR/concat.txt"
for seg in "$WORK_DIR"/seg_*_silent.mp4; do echo "file '$seg'" >> "$WORK_DIR/concat.txt"; done
ffmpeg -y -f concat -safe 0 -i "$WORK_DIR/concat.txt" -c copy "$WORK_DIR/stylized_silent.mp4"
# Then mux as above
```

---

## 13\. Deliverable layout

After a successful run, `$WORK_DIR/` contains:

```
work/
├── preprocessed.mp4                         # validated input (copy of source for demo)
├── audio.aac                                # extracted audio
├── kf1_raw.jpg, kf2_raw.jpg                 # raw anchor frames at ~33% / ~66%
├── kf1_styled.jpg, kf2_styled.jpg           # (optional) downloaded Seedream outputs for QC
├── stylized_silent.mp4                      # Seedance 2.0 output (silent)
└── unity_handdrawn_final.mp4                # FINAL deliverable — hand-drawn, with audio
```

That MP4 is the artifact you show Unity\. Everything else is throwaway\.

---

## 14\. Resolution reference \(Seedance 2\.0\)

|Aspect ratio|480p \(W×H\)|720p \(W×H\)|
|---|---|---|
|16:9|864×496|1280×720|
|4:3|752×560|1112×834|
|1:1|640×640|960×960|
|**3:4 \(demo clip\)**|**560×752** \(native — no resize\)|**834×1112**|
|9:16|496×864|720×1280|
|21:9|992×432|1470×630|

1080p/4K buckets also exist; see the ModelArk video generation tutorial[\[Video generation\]](https://docs.byteplus.com/en/docs/modelark/2371066)\.

---

## 15\. End\-to\-end reference script

```python
#!/usr/bin/env python3
"""Zero-infra Unity hand-drawn style pipeline — runner."""
import os, base64, subprocess, requests, time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()
WORK = os.environ["WORK_DIR"]
os.makedirs(WORK, exist_ok=True)

ARK_BASE = os.environ["ARK_BASE_URL"]
ARK_KEY  = os.environ["ARK_API_KEY"]

# ---------- small helpers ----------
def run(cmd):
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)

def b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")

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

def download(url: str, out: str):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)

# ---------- prompts (paste §10.1 / §11.1 verbatim here) ----------
KEYFRAME_PROMPT = """..."""
SEEDANCE_PROMPT = """..."""

# ---------- run ----------
if __name__ == "__main__":
    # 2. preprocess (no-op copy for demo)
    run(["cp", os.environ["INPUT_VIDEO"], f"{WORK}/preprocessed.mp4"])
    # 3. audio extract
    run(["ffmpeg", "-y", "-i", f"{WORK}/preprocessed.mp4",
         "-vn", "-c:a", "copy", f"{WORK}/audio.aac"])
    # 5. extract raw keyframes at 2s and 5s
    run(["ffmpeg", "-y", "-ss", "2", "-i", f"{WORK}/preprocessed.mp4",
         "-frames:v", "1", "-q:v", "2", f"{WORK}/kf1_raw.jpg"])
    run(["ffmpeg", "-y", "-ss", "5", "-i", f"{WORK}/preprocessed.mp4",
         "-frames:v", "1", "-q:v", "2", f"{WORK}/kf2_raw.jpg"])
    # 5.1 upload style ref + video (only things Seedance needs as URLs)
    STYLE_REF_URL = tmp_upload(os.environ["STYLE_REF"], "style.jpeg")
    VIDEO_URL     = tmp_upload(f"{WORK}/preprocessed.mp4", "preprocessed.mp4")
    print("STYLE_REF_URL =", STYLE_REF_URL)
    print("VIDEO_URL     =", VIDEO_URL)

    # 6. Seedream i2i — parallel, images as base64, returns ModelArk URLs
    def do_kf(raw_path, seed):
        r = requests.post(f"{ARK_BASE}/images/generations",
            headers={"Authorization": f"Bearer {ARK_KEY}", "Content-Type": "application/json"},
            json={"model": "doubao-seedream-5-0-lite-260128",
                  "prompt": KEYFRAME_PROMPT,
                  "image": [b64_image(raw_path), b64_image(os.environ["STYLE_REF"])],
                  "size": "2K", "response_format": "url",
                  "watermark": False, "seed": seed}, timeout=120)
        r.raise_for_status()
        return r.json()["data"][0]["url"]

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(do_kf, f"{WORK}/kf1_raw.jpg", 42)
        f2 = ex.submit(do_kf, f"{WORK}/kf2_raw.jpg", 43)
        KF1_STYLED_URL = f1.result()
        KF2_STYLED_URL = f2.result()
    print("KF1_STYLED_URL =", KF1_STYLED_URL)
    print("KF2_STYLED_URL =", KF2_STYLED_URL)

    # 7. Seedance
    r = requests.post(f"{ARK_BASE}/contents/generations/tasks",
        headers={"Authorization": f"Bearer {ARK_KEY}", "Content-Type": "application/json"},
        json={"model": "dreamina-seedance-2-0-260128",
              "content": [
                {"type": "text",      "text": SEEDANCE_PROMPT},
                {"type": "image_url", "image_url": {"url": STYLE_REF_URL},  "role": "reference_image"},
                {"type": "image_url", "image_url": {"url": KF1_STYLED_URL}, "role": "reference_image"},
                {"type": "image_url", "image_url": {"url": KF2_STYLED_URL}, "role": "reference_image"},
                {"type": "video_url", "video_url": {"url": VIDEO_URL},      "role": "reference_video"},
              ],
              "ratio": "3:4", "duration": 8, "seed": 42,
              "camera_fixed": False, "watermark": False}, timeout=60)
    r.raise_for_status()
    task_id = r.json()["id"]
    print("Seedance task:", task_id)
    while True:
        g = requests.get(f"{ARK_BASE}/contents/generations/tasks/{task_id}",
            headers={"Authorization": f"Bearer {ARK_KEY}"}, timeout=30)
        g.raise_for_status()
        d = g.json()
        print("Seedance:", d.get("status"))
        if d.get("status") == "succeeded":
            download(d["content"]["video_url"], f"{WORK}/stylized_silent.mp4"); break
        if d.get("status") in ("failed", "cancelled"):
            raise SystemExit(f"Seedance failed: {d}")
        time.sleep(10)

    # 8. audio mux
    run(["ffmpeg", "-y",
         "-i", f"{WORK}/stylized_silent.mp4",
         "-i", f"{WORK}/audio.aac",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-map", "0:v:0", "-map", "1:a:0",
         "-shortest", "-movflags", "+faststart",
         f"{WORK}/unity_handdrawn_final.mp4"])
    print("DONE →", f"{WORK}/unity_handdrawn_final.mp4")
```

**To run:** fill `.env`, drop `unity-5.mp4` and `handDrawnStyle.jpeg` in the project root, `pip install requests python-dotenv python-dotenv`, paste the two prompts into the script, and `python run_pipeline.py`\. That's it — no cloud account beyond ModelArk itself\.

---

## 16\. Error handling cheat sheet

|Symptom|Cause|Fix|
|---|---|---|
|Seedance returns "URL not reachable"|Temp\-host URL hasn't propagated, or `transfer.sh` returned an error page|`curl -sSI "$URL"` first; swap to a different temp host or use the cloudflared tunnel path\.|
|Seedream: `"image" parameter not supported`|Used `dola-seedream-5-0-pro-260628`|Switch to `doubao-seedream-5-0-lite-260128`\.|
|Payload too large / 413 on Seedream|One of the base64 images is oversized|Ensure input JPEGs are \<1 MB; if not, re\-encode with `ffmpeg -i raw.jpg -q:v 4 small.jpg`\. \(Keyframes from ffmpeg `-q:v 2` already come out at \~200–400 KB, so this is rare\.\)|
|Seedance output looks 3D / partially un\-styled|Single anchor insufficient, or forbidden list not strong enough|Add a 4th anchor at \~90% \(repeat Module 6 for t=7s\); append "stronger pencil hatching" to the style section; retry with different `seed`\.|
|Temporal flicker on road hatching|`camera_fixed` wrong, or segment too long|Confirm `camera_fixed: False` \(correct for a dolly shot\); shorten per\-call duration via scene split\.|
|Audio slightly out of sync|Different time bases|Force re\-encode \(`-c:a aac -b:a 192k`\) and re\-mux; if persistent add `-vsync cfr -r 24`\.|
|`transfer.sh` is down / blocked|Network restriction|Swap the `tmp_upload` body to `0x0.st` or use the `cloudflared` path in §4\.|
|Content\-safety rejection|Rare for driving footage; usually a false positive|Resubmit; if repeatable confirm no watermarks/logos in source\.|

---

## 17\. Testing instructions for the coding agent

1. Boot the environment \(ffmpeg present, `pip install requests python-dotenv`, `.env` populated\)\.

2. Run the script end\-to\-end once unmodified\.

3. After the run verify programmatically where possible, visually where necessary:

    - `work/unity_handdrawn_final.mp4` exists, is a valid MP4 \(`ffprobe` exits 0\), duration ≈ 8\.0 s, resolution = 560×752, has an audio stream\.

    - Extract first frame: `ffmpeg -ss 0 -i work/unity_handdrawn_final.mp4 -frames:v 1 work/qc_frame0.jpg` — must read as pencil\-sketch \(not the raw 3D render\)\.

    - Spot\-check frames at t=2, t=5, t=7 similarly\.

4. If visual QC passes, you're done — hand `unity_handdrawn_final.mp4` to Unity for demo\.

5. Optional: change `"resolution": "720p"` and add the Module 2 upscale step \(`scale=834:1112`\) to test the resize path\.

6. Optional: feed a \>15 s clip with cuts, enable PySceneDetect, and validate the multi\-segment path\.

---

## 18\. When to add infrastructure back \(production at scale\)

This zero\-infra variant is intentionally minimal\. It is the fastest way to prove the pipeline works and produce a demo MP4, and it has **zero vendor lock\-in beyond ModelArk itself**\. Add storage and media\-processing infrastructure back only when one of the following becomes true:

|Signal|What to add and why|
|---|---|
|**You need to keep outputs durably or share them with a team**|Put inputs/outputs in **TOS** \(or S3/R2\)\. TOS is just durable storage — it doesn't replace media processing, but it replaces the `transfer.sh` hack with permanent URLs and lifecycle policies\.|
|**Concurrent jobs \> \~10 / sec or long\-form content**|Add **BytePlus VOD** for managed transcode, audio extract, scene segmentation, and timeline editing\. VOD runs these as autoscaled async workflows, so you don't have to build/operate a worker fleet\.|
|**End\-user playback in a product \(HLS/DASH, adaptive bitrate, DRM\)**|Add VOD for ABR\-ladder packaging, Widevine/FairPlay/PlayReady DRM, and the built\-in player SDK\. Rolling this yourself is a sizable project\.|
|**Multi\-tenant / production SaaS**|Add VOD \(media workflows \+ CDN \+ playback\) in front of TOS \(storage\)\. VOD can process directly against a TOS bucket via DirectUrl mode, so there's no data migration when you grow from TOS\-only into TOS\+VOD[\[Processing media files in TOS buckets\]](https://docs.byteplus.com/en/docs/byteplus-vod/process-media-files-stored-in-tos-buckets)\.|

### Migration path

The prompts, seed strategy, anchor logic, and Seedance call shape in this doc are **identical** to both the VOD\-free \(ffmpeg \+ S3\) and full VOD variants\. When you promote from demo to production, you only swap the how of media processing \(ffmpeg → VOD workflows\) and the where of durable storage \(temp host → TOS\+VOD\)\. The ModelArk calls stay the same — just point `image_url`/`video_url` at VOD `GetPlayInfo` signed URLs instead of temp\-host URLs\. Prompt iteration done on this zero\-infra variant transfers 1:1 to production\.

**Rule of thumb:**

- **One\-off demo to Unity / \<10 runs total** → this doc \(zero\-infra\)\. You can finish today\.

- **Internal tool, \<100 jobs/day, no end\-user playback** → the VOD\-free ffmpeg\+S3 doc \(sibling\)\.

- **Production SaaS, multi\-tenant, user\-facing playback, \>100 jobs/day or long\-form** → the BytePlus Edition doc with full VOD\.

