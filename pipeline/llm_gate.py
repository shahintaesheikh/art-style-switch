#!/usr/bin/env python3
"""LLM Gate — analyzes the reference style image via VLM and generates
style-specific KEYFRAME_PROMPT and SEEDANCE_PROMPT tailored to that medium/art-style.

This step sits between probing (§5) and the QC Gate / Seedream stylizing (§10).
It replaces the hardcoded prompts in prompts.py with dynamically generated ones
that describe the exact medium, line work, color treatment, and texture of the
user-provided style reference image.

The style reference is referenced via the ModelArk Private Asset Library
(asset://<id> URI) — the asset is uploaded once in the ModelArk dashboard and
referenced by URI, not sent as base64. See .pi/skills/assets-api.md.

Usage:
    from pipeline.llm_gate import generate_style_prompts
    prompts = generate_style_prompts(ARK_API_KEY, "asset://asset-...")
    # prompts["keyframe"]  → str (KEYFRAME_PROMPT)
    # prompts["seedance"]  → str (SEEDANCE_PROMPT)
    # prompts["style_analysis"]  → dict (raw VLM analysis, for debugging)
"""

from __future__ import annotations

import json
import os

import requests

# Corporate TLS interception (SealSuite SWG) — use OS trust store
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VLM_MODEL = os.environ.get("LLM_GATE_MODEL", "dola-seed-2-1-turbo-260628")
INFERENCE_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# ---------------------------------------------------------------------------
# System prompt for the style-analysis VLM call
# ---------------------------------------------------------------------------

STYLE_ANALYSIS_SYSTEM_PROMPT = """\
You are a professional art director and prompt engineer. You will receive a single reference image — a style guide for a video-style-transfer pipeline.

Your job: analyze the image's visual medium, rendering technique, line work, color treatment, shading, and texture in meticulous detail, then output a structured JSON description.

## Analysis dimensions (be specific — avoid vague terms like "artistic" or "stylized")

1. **medium**: What physical medium does this look like? (e.g., "pencil and colored-pencil sketch on off-white paper", "charcoal on rough newsprint", "watercolor on hot-press paper", "oil pastel on canvas", "ink wash on rice paper", "digital cel-shading with vector outlines", "gouache on tinted paper", "marker pen on sketch paper", "soft pastel on textured paper", "acrylic impasto on canvas", "scratchboard with white ink", "linocut print", "pointillism with fine-liner pen", "chalk pastel on black paper", "colored pencil on toned tan paper", "digital watercolor with paper texture overlay", "pen and ink with cross-hatching on Bristol board"). Be specific about the substrate and tool.

2. **line_work**: Describe the line quality in detail. (e.g., "fine graphite hatching and cross-hatching; sketchy, slightly irregular outlines as if drawn by hand with a soft 2B pencil; no clean vector lines", "bold black ink outlines, uniform width, with minimal internal detail — cartoon/comic style", "delicate, thin pen lines with tight parallel hatching for shading; very precise and controlled", "wide charcoal strokes with smudged edges; no sharp outlines; soft and atmospheric", "no visible outlines; forms defined by color patches and value contrast — painterly approach", "scratchy, jagged lines with variable width; energetic and loose", "stippled dots building up value — no lines at all except for edges")

3. **color_palette**: Describe the color treatment. (e.g., "crimson red colored-pencil, ultramarine blue, light cerulean sky wash, graphite greys, cream/off-white paper showing through; hatched, not flat fill", "muted earth tones — burnt sienna, raw umber, olive green, warm ochre, cream highlights; low saturation, warm palette", "high-saturation primaries — bright cyan, magenta, yellow, with black keylines; flat cel-shaded areas, no gradients", "monochromatic sepia — warm brown ink on cream paper; values from pale tan to dark umber, no other hues", "cool palette — cerulean, viridian, payne's grey, titanium white; thin washes over visible pencil underdrawing", "vibrant gouache — saturated cadmium red, cobalt blue, permanent green, yellow ochre; opaque flat areas with visible brush strokes")

4. **shading_technique**: How are shadows and volume created? (e.g., "pencil stroke density — lighter areas have wide hatch spacing, shadows have dense cross-hatching; no smooth gradients", "watercolor washes — wet-on-wet for soft shadows, dry brush for texture; transparent layers building up value", "flat cel-shading — two-tone: a base color and a darker shadow shape with a hard edge; no gradient between them", "charcoal smudging — value built by rubbing/pressing, not by distinct strokes; very smooth transitions", "stippling — dots of varying density; no lines, no smudging, no hatching", "hatching with fine-liner — parallel lines in one direction, cross-hatching for deeper shadows; consistent line spacing", "oil-paint impasto — thick visible brush strokes, directional stroke patterns following form; shadow is darker paint, not transparency")

5. **texture**: Surface quality and paper/stroke artifacts. (e.g., "visible paper tooth texture under the color; slight hand-drawn imperfection — wobbly lines, minor stroke overflow, eraser smudges", "smooth and polished — no visible paper grain, no brush marks, perfectly uniform surfaces", "rough watercolor paper texture visible through thin washes; salt-grain texture in wet areas", "visible canvas weave and thick paint ridges; directional brush strokes", "digital-perfect uniform fill — no texture variation, no hand-made artifacts", "grainy charcoal dust settled in paper valleys; soft edges where powder was blended", "scratchy — the pen has slightly torn the paper surface in places, creating rough edges")

6. **overall_style**: One short label. (e.g., "hand-drawn pencil sketch", "watercolor painting", "cel-shaded vector art", "charcoal drawing", "ink wash painting", "digital anime", "oil painting", "pastel drawing", "pen-and-ink illustration", "gouache painting", "colored-pencil illustration", "graphite realism")

## Output format
Return exactly one JSON object with no prose, no markdown fences, no commentary outside the JSON:

{
  "style_label": "short label (e.g. 'hand-drawn pencil sketch')",
  "medium": "specific medium description (1-3 sentences)",
  "line_work": "detailed line work description (2-4 sentences)",
  "color_palette": "detailed color palette description (2-4 sentences)",
  "shading_technique": "detailed shading description (2-4 sentences)",
  "texture": "detailed texture description (2-4 sentences)",
  "key_visual_elements": ["list", "of", "distinctive", "visual", "traits", "that", "define", "this", "style"]
}
"""


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

KEYFRAME_PROMPT_TEMPLATE = """\
Redraw the provided gameplay frame in the exact {style_label} style of the reference image.

Style requirements (match the reference image exactly):
- Medium: {medium}
- Line work: {line_work}
- Color: {color_palette}
- Shading: {shading_technique}
- Texture: {texture}

Content preservation (must match the raw source frame exactly — do not invent, remove, or move anything):
- Preserve camera perspective, focal length, and road vanishing point exactly.
- Preserve the position, scale, orientation, and count of every object.
- Preserve the composition and framing 1:1; do not zoom, crop, rotate, or letterbox.
- Preserve the approximate colors of the source, only reinterpreted through the {style_label}.

Forbidden: photorealism, 3D renders, smooth gradients, airbrushed look, anime cel shading, cartoon outlines with flat fills, watercolor blobs without pencil lines, photography, AI-overprocessed smooth textures, extra lens flare, extra motion blur not in the source, any signature, watermark, or text.

Output: 3:4 portrait matching the input aspect ratio, no watermark, no border, no text, no signature.
"""

SEEDANCE_PROMPT_TEMPLATE = """\
【Global setup】Output portrait 3:4. The reference_video provides camera, motion, composition, timing, object positions, occlusion, movement, road perspective, and all editing structure ONLY — do NOT carry over any of its 3D-rendered look, flat shading, smooth gradients, or game-engine lighting. The three reference_images define the visual style and color treatment for the entire clip — every frame of the output must look like it was {style_label} in that exact style. Object positions, scale, and motion come ONLY from reference_video — never copy object placement or composition from the reference_images.

【Per-frame style rules】Every single output frame must be fully redrawn in the {style_label} of the reference images:
- Medium: {medium}
- Line work: {line_work}
- Color: {color_palette}
- Shading: {shading_technique}
- Texture: {texture}
- The rendering must animate coherently with camera motion — it must not flicker or jitter between frames.

【Style consistency anchors】
- @Image 1 (frame 0 — style reference) anchors the opening style.
- @Image 4 (frame at ~29% anchor) and @Image 6 (frame at ~57% anchor) anchor the mid-clip style as new elements enter and the perspective deepens.
- @Image 8 (frame at 100% anchor) anchors the closing style.
Interpolate the style smoothly between these anchors; style, stroke weight, paper color, and palette must remain perceptually identical from the first frame to the last — no drift toward 3D, photorealism, or a different art style at any point.

【Content preservation — strict】
- Preserve the camera motion exactly as in reference_video; preserve the perspective, object placement, scale, and timing.
- Preserve the exact colors of objects from the source.
- Do NOT add people, animals, signs, UI elements, lens flares, speed lines that weren't in the source, or any extra objects. Do NOT remove or relocate any object present in the source.
- Do NOT change the pacing, timing, or action.

【Forbidden】Photorealism, 3D-rendered look, smooth gradients, airbrushed shading, anime cel shading, cartoon outlines with flat fills, watercolor-only without underdrawing, photography, AI over-smoothed textures, flicker between frames, jittery outlines, morphing objects, warping buildings, tire/shape deformation on the cars, any signature, watermark, text, or on-screen UI.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"}


def _validate_asset_uri(uri: str) -> None:
    """Reject local file paths — the LLM gate requires a ModelArk asset:// URI."""
    if uri.startswith("asset://"):
        return  # OK — ModelArk Private Asset Library reference
    if uri.startswith("https://") or uri.startswith("http://"):
        return  # OK — https URL works as a fallback
    # Looks like a local path — the user's request is to use the assets API
    raise ValueError(
        f"LLM gate requires an asset:// URI (or https URL), not a local path: {uri!r}.\n"
        f"Upload the style reference image to the ModelArk Asset Library (console) and\n"
        f"set STYLE_REF_URI=asset://<id> in your .env file. See .pi/skills/assets-api.md."
    )


# ---------------------------------------------------------------------------
# VLM call
# ---------------------------------------------------------------------------

def _call_vlm_analyze(api_key: str, style_ref_uri: str,
                      timeout: int = 120) -> dict:
    """Send the reference image (via ModelArk asset:// URI) to the VLM
    and return the parsed style analysis JSON."""
    r = requests.post(
        f"{INFERENCE_BASE_URL}/chat/completions",
        headers=_headers(api_key),
        json={
            "model": VLM_MODEL,
            "messages": [
                {"role": "system", "content": STYLE_ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze the style of this reference image in detail."},
                        {"type": "image_url", "image_url": {"url": style_ref_uri}},
                    ],
                },
            ],
            "max_tokens": 4096,
            "temperature": 0.3,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    text = body["choices"][0]["message"]["content"]

    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_style_prompts(api_key: str, style_ref_uri: str,
                           timeout: int = 120) -> dict[str, str]:
    """Analyze the reference style image (via ModelArk asset:// URI) and
    generate style-specific prompts.

    Args:
        api_key: BytePlus ModelArk API key (Bearer).
        style_ref_uri: ModelArk asset:// URI for the reference style image,
                       e.g. "asset://asset-20260222234430-mxpgh".
        timeout: Timeout in seconds for the VLM call.

    Returns:
        dict with keys:
            "keyframe": str — KEYFRAME_PROMPT for Seedream i2i
            "seedance": str — SEEDANCE_PROMPT for Seedance video generation
            "style_analysis": dict — the raw analysis from the VLM (for debugging)

    Raises:
        ValueError: if style_ref_uri is a local file path.
        requests.RequestException: on HTTP/network failures.
        json.JSONDecodeError: if the VLM response is not valid JSON.
        KeyError: if the response is missing required fields.
    """
    _validate_asset_uri(style_ref_uri)
    analysis = _call_vlm_analyze(api_key, style_ref_uri, timeout=timeout)

    # Fill in the prompt templates
    keyframe_prompt = KEYFRAME_PROMPT_TEMPLATE.format(
        style_label=analysis.get("style_label", "hand-drawn style"),
        medium=analysis.get("medium", ""),
        line_work=analysis.get("line_work", ""),
        color_palette=analysis.get("color_palette", ""),
        shading_technique=analysis.get("shading_technique", ""),
        texture=analysis.get("texture", ""),
    )

    seedance_prompt = SEEDANCE_PROMPT_TEMPLATE.format(
        style_label=analysis.get("style_label", "hand-drawn"),
        medium=analysis.get("medium", ""),
        line_work=analysis.get("line_work", ""),
        color_palette=analysis.get("color_palette", ""),
        shading_technique=analysis.get("shading_technique", ""),
        texture=analysis.get("texture", ""),
    )

    return {
        "keyframe": keyframe_prompt,
        "seedance": seedance_prompt,
        "style_analysis": analysis,
    }