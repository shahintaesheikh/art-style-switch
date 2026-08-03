---
name: seedream-5-user-guide
description: Usage manual and prompt engineering guide for ByteDance's Seedream 5.0 Pro image generation model. Use this skill when generating or refining prompts for Seedream 5.0 Pro (i2i or t2i), including image-to-image style transfer, interactive editing, multi-image fusion, text rendering, layer separation, or any image generation task using the Seedream 5.0 Pro model via BytePlus ModelArk.
---

# Seedream 5.0 Pro — Usage Manual & Prompt Engineering Guide

Seedream 5.0 Pro is ByteDance's flagship multimodal image creation model, designed for controllable production workflows rather than one-shot generation. It excels at interactive precision editing, high-density information visualization, native multilingual text rendering, cinematic imagery, and portrait fidelity.

This guide covers everything needed to use Seedream 5.0 Pro effectively via the BytePlus ModelArk API.

---

## Quick Reference Card

| Item | Value |
|------|-------|
| **Model ID (BytePlus)** | `dola-seedream-5-0-pro-260628` |
| **Model ID (Volcengine/CN)** | `doubao-seedream-5-0-pro-260628` |
| **Endpoint** | `POST https://ark.ap-southeast.bytepluses.com/api/v3/images/generations` |
| **Regions** | `ap-southeast-1`, `eu-west-1` |
| **Auth** | API Key (Bearer token) |
| **Playground** | https://console.byteplus.com/ark/region:ark+ap-southeast-1/experience/vision?modelId=dola-seedream-5-0-pro-260628 |
| **API Docs** | https://docs.byteplus.com/en/docs/ModelArk/1541523 |
| **User Guide** | https://docs.byteplus.com/en/docs/ModelArk/1824121 |
| **Tech Blog** | https://seed.bytedance.com/en/blog/beyond-generation-it-understands-design-introducing-seedream-5-0-pro |

---

## Core Capabilities

Seedream 5.0 Pro delivers four major breakthroughs over previous models:

1. **Complex Information Visualization** — Transforms data, concepts, dense text, and report content into professional infographic layouts. Handles timelines, charts, diagrams, posters, and UI mockups with clear information hierarchy.
2. **Interactive Precision Editing** — Supports point selection, lasso/box selection, sketch/doodle input, anchor-based positioning, color (Hex code) and material replacement, layer separation, and multi-image fusion for pixel-level local edits.
3. **Realistic Imagery & Portrait Textures** — Accurately reproduces real-world lighting, reflections, refractions, skin textures, and materials (glass, metal, wood, leather, fabric). Balances CG expressiveness with photographic quality.
4. **Native Multilingual Support** — 15 languages natively supported for both prompt input and in-image text rendering: Arabic, English, Russian, Indonesian, Spanish, German, Turkish, Portuguese, Malay, Vietnamese, French, Japanese, Korean, Tagalog, Thai. Right-to-left scripts (Arabic) and accent marks render correctly. Other languages work but with weaker text rendering and cultural understanding.

---

## API Parameters

### Supported Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `model` | string | Yes | — | `dola-seedream-5-0-pro-260628` (BytePlus) or `doubao-seedream-5-0-pro-260628` (Volcengine CN) |
| `prompt` | string | Yes | — | Text prompt describing what to generate or how to edit. Maximum detail = best results. |
| `image` | string/array | No | — | Reference image(s) as URL or Base64. Up to **10 images**. First image is free; each additional is $0.003. |
| `size` | string | No | `1024x1024` | Output size. Two methods: pixel dimensions (`WxH`) or resolution tier (`1K`/`2K`). |
| `output_format` | string | No | `jpeg` | `png` or `jpeg`. Use `png` for layer separation outputs (alpha channel). |
| `response_format` | string | No | `url` | `url` (returns image URL) or `b64_json`. |
| `watermark` | boolean | No | `false` | Whether to add a watermark. Set to `false` for clean outputs. |
| `optimize_prompt_options` | object | No | `{"mode":"standard"}` | Prompt optimization config. Only `standard` mode supported (Pro does not support `fast` mode). |
| `n` / num_images | integer | No | 1 | Number of images to generate. |

### NOT Supported (Do NOT pass these)

- `stream` — no streaming/progressive output; Pro uses parallel processing.
- `sequential_image_generation` / `sequential_image_generation_options` — not supported.
- `tools` (web search, etc.) — not supported.
- `guidance_scale` — not configurable.

### Image Input Constraints

- **Formats**: jpeg, png, webp, bmp, tiff, gif, heic, heif
- **Size**: ≤ 30 MB per image
- **Dimensions**: Width and height > 14px; total pixels ≤ 6000×6000 (36M)
- **Aspect ratio**: [1/16, 16]
- **Max images**: 10 reference images

---

## Output Resolution & Pricing

### Resolution Tiers

Billing is determined by total pixel count, not by the label:

| Tier | Total Pixels | Price per Output | Common Sizes |
|------|-------------|-----------------|--------------|
| **1.5K** | ≤ 2.36M px | **$0.045** | 1:1 → 1536×1536; 4:3 → 1776×1328; 16:9 → 2048×1152 |
| **2K** | > 2.36M px | **$0.09** | 1:1 → 2048×2048; 4:3 → 2360×1770; 16:9 → up to 2720×1530 (~2.7K) |

When using `size` with pixel dimensions, total output pixels must be within `[1280×720 (921,600), 2048×2048×1.05×1.05 (4,624,220)]`. Aspect ratio range is [1/16, 16].

### Recommended Preset Sizes

| Resolution | 1:1 | 4:3 | 3:4 | 16:9 | 9:16 | 3:2 | 2:3 |
|-----------|-----|-----|-----|------|------|-----|-----|
| **1K** | 1024×1024 | 1184×896 | 896×1184 | 1376×768 | 768×1376 | 1248×832 | 832×1248 |
| **2K** | 2048×2048 | 2368×1792 | 1792×2368 | 2752×1536 | 1536×2752 | 2496×1664 | 1664×2496 |

When using resolution tier (`1K`/`2K`), describe the desired aspect ratio, shape, or purpose in the prompt, and the model will determine the exact dimensions.

### Pricing Formula

**Total = Input images cost + Output image cost**
- First reference image: **free**
- Each additional reference image: **$0.003**
- Output (≤ 2.36M px): **$0.045**
- Output (> 2.36M px): **$0.09**

### Price Comparison Highlights

- vs Nano Banana 2 at 1:1: Pro is ~55% cheaper at 1.5K, ~11% cheaper at 2K
- vs GPT Image 2 High: Pro is 3.7x–4.7x cheaper
- vs GPT Image 2 Medium: Comparable for output-only, Pro wins when reference images are involved (Pro's first ref is free vs GPT's ~$0.013/image)

---

## Prompt Engineering Guide

### Core Principle: Be Specific and Structured

The more specific the prompt, the better the result. Structure your prompts to cover:

1. **Subject** — What is the main subject? Describe appearance, pose, expression, materials.
2. **Scene/Setting** — Environment, background, location, context.
3. **Style** — Art style, photography style, rendering quality, reference aesthetic.
4. **Lighting** — Light direction, quality (soft/harsh), color temperature, time of day.
5. **Composition** — Camera angle, shot type (close-up/wide/macro), depth of field, framing.
6. **Color/Mood** — Color palette, emotional tone, atmosphere.
7. **Technical details** — Resolution hints (e.g., "highly detailed," "8K," "cinematic"), specific materials.

### Prompt Structure Template

```
[Subject description in detail], [setting/background], [style/aesthetic],
[lighting description], [composition/camera], [color palette], [mood/atmosphere],
[technical quality tags]
```

### Text-to-Image Examples

**Cinematic scene:**
> A panning shot of a cyclist racing down a coastal road at golden hour. The rider and the bicycle are clear and sharp, the background street is stretched into horizontal motion blur, and the wheel spokes have rotational blur to convey a sense of speed. Warm sunset lighting, lens flare, cinematic color grading, photorealistic, shallow depth of field.

**Infographic:**
> A visual infographic chronicling scientific research at Antarctica's Qinling Station. Place the main Qinling Station building at the center. Surround it with a timeline of research station development, a bar chart comparing the sizes of five research stations, a pie chart of the station's energy sources, and a line chart of monthly sunshine. Supplement with realistic photos of research equipment, a summer weather panel, a seven-step fieldwork flowchart, and on-site sampling photography. Clean professional layout, cool blue and white color palette, data visualization style.

**E-commerce product shot:**
> A premium skincare bottle on a marble pedestal, soft studio lighting from the left, pale pink and cream background with subtle floral shadows, water droplets on the bottle surface reflecting light, high-end commercial product photography, 45-degree angle shot, shallow depth of field, clean minimal composition.

**Marketing poster:**
> A 16:9 Winter Christmas Sale poster on a vintage scroll background. Bold headline "WINTER SALE" in decorative serif font at top, "Up to 50% OFF" in large bold text in center, event dates "Dec 20–25" in handwritten script at bottom. Deep red and gold color scheme, snowflake decorations, evergreen branches, warm candlelight glow, commercial advertising aesthetic, well-judged text spacing.

**Portrait:**
> A cinematic portrait of a young woman in her late 20s, natural matte skin texture with subtle facial lines visible, soft window light from the left creating gentle Rembrandt lighting, thoughtful contemplative expression, wearing a cream turtleneck sweater, blurred bookshelf background, 85mm lens, f/1.8 aperture, film grain, photorealistic.

### Style Keywords

Use these to steer the visual aesthetic:

| Category | Keywords |
|----------|----------|
| **Photography** | photorealistic, cinematic, film grain, bokeh, golden hour, studio lighting, high-speed photography, macro, panning shot, motion blur, HDR, monochrome, black and white |
| **Design** | flat design, minimalist, editorial layout, typographic poster, grid layout, Swiss design, Bauhaus, Art Deco, vintage poster, modernist, brutalist, isometric, infographic style |
| **Art/Illustration** | watercolor, oil painting, pencil sketch, ink drawing, digital art, concept art, pixel art, anime, Studio Ghibli style, cyberpunk, steampunk, pop art, ukiyo-e, line art |
| **Rendering** | 3D render, Octane render, Unreal Engine, ray tracing, volumetric lighting, subsurface scattering, physically based rendering |
| **Commercial** | product photography, advertising campaign, editorial fashion, lookbook, lifestyle photo, catalog shot, hero image |

### Aesthetic Quality Boosters

Add these tags at the end of prompts for improved results:
- `highly detailed`, `intricate details`, `sharp focus`, `professional`
- `masterpiece`, `best quality`, `award-winning`
- `8k resolution`, `ultra HD`, `photorealistic`
- `natural lighting`, `accurate colors`, `true-to-life materials`

---

## Interactive Editing Techniques

Seedream 5.0 Pro's key differentiator is controllable local editing. When providing an input image, use these techniques in your prompt:

### 1. Bounding Box / Region Selection

Use colored annotation boxes to designate edit regions and describe what goes in each:

> Red box: A huge blue-furred monster head with a ferocious squished expression, gazing at the bubble ahead. Green box: A transparent bubble reflecting indoor lights. Yellow box: A large warm gray-beige yarn ball. Blue box: A stack of building blocks including a warm dark gray arch.

Draw colored rectangles on the image and reference them in the prompt. Each element strictly respects its coordinate boundaries.

### 2. Point Selection

Use coordinate points to target specific locations in multi-image workflows:

> Image 1 <point>518 135</point> replace photo with Image 2; Image 1 <point>556 349</point> replace photo with Image 3.

Points are specified as `<point>X Y</point>` with pixel coordinates from the top-left origin.

### 3. Arrows & Annotations

Draw arrows or annotation frames on the image pointing to target regions, then describe the desired change in natural language. This is intuitive for non-technical users.

### 4. Anchor Editing (Position Editing)

Use anchor points for high-precision edits in structured layouts (e.g., chessboards, grids, arrays). Reference positions by row/column or spatial descriptions:

> Shift the red chariot at the bottom left one square to the right, and move the black pawn on the second line counting from the left side of Black's position one square downwards.

Best when objects are arranged in clear rows and columns.

### 5. Sketch Editing

Provide rough doodles, color blocks, lines, or simple sketches as reference images. Combined with text instructions, the model renders them into detailed objects.

Workflow: Draw a rough layout sketch → upload as reference image → prompt describes what each sketched region should become.

> [Sketch reference] A spring outing poster for Sanli Elementary School. The felt and stitching textures should be visible. Place departure time "8:00 AM" in the top-left area and the packing list in the bottom-right area as marked.

### 6. Precise Color & Material Editing

Specify Hex color codes directly in the prompt, or provide a separate color palette reference image. Combine with material descriptions:

> Change the pumpkins to an alternating pattern of dark green (#3E4A2E) and turmeric yellow (#DB973E). Give the background typography an embroidered texture.

> Using the material from Image 1 and the color swatch from Image 2, modify the sofa in Image 3.

### 7. Multi-Image Fusion

Fuse objects, styles, and materials from multiple reference images into one composition:

> Precisely cut out the objects from my 7 white-background reference photos and compose them, per the specified layout, into a real still-life photograph. Ensure correct perspective, light-and-shadow, and spatial relationships; faithfully reproduce material details such as wood grain, leather, lace, glass jelly, and feathers, creating a high-quality image where reality intertwines with playfulness and retro blends with modern.

> Generate a full-body shot of the person in Image 3, and adjust her pose so her right hand motion matches Image 1, holding the first speaker from Image 4, and her leg motion matches Image 2.

> Combine the people from Images 2 to 6 into a group photo referencing the positioning in Image 1. The people should have happy expressions, with trees and a cafe storefront in the background.

**Key tip:** Reference images are numbered in the order passed (Figure 1 = first image in the array, Figure 2 = second, etc.).

### 8. Layer Separation

Split an image into independently editable layers (background + N element layers), output as PNG files with alpha/transparency channel. Returns 2–20 images (billed per image).

> Separate this poster into independent layers: the parrot, the text elements, the background, and all decorative elements.

Layers can be freely dragged, scaled, and recomposed downstream. Background areas previously obscured by foreground objects are seamlessly inpainted.

> Replace the clothing in Figure 1 with the clothing in Figure 2.

---

## Multilingual Text in Images

Seedream 5.0 Pro natively renders text in 15 languages. For best results:

- Write the text you want in the image **exactly as it should appear** in the target language, within the prompt.
- For Arabic: Use correct RTL script; the model handles right-to-left rendering automatically.
- For accented languages (Spanish, French, Portuguese, etc.): Include accent marks directly (e.g., "PASIÓN", "CRÉATION FRANÇAISE").
- For East Asian languages (Japanese, Korean, Thai): Native character rendering is stable.
- Describe the text's visual style: font type (serif/sans-serif/handwritten/bold), size, position, color.
- Be honest: text rendering is **significantly improved but not perfect** — small text may still have occasional typos. Recommend manual proofing for production use.

Example:
> A storefront sign with Arabic text "أهلاً بكم" (Welcome) in elegant gold calligraphy, warm evening lighting, Middle Eastern bazaar atmosphere.

> A Korean neon sign reading "24시 영업 중" (Open 24 hours) in bright pink and blue, cyberpunk alley at night, rain-slicked streets, reflections.

---

## Industry-Specific Guidance

### E-Commerce
- Use product images as reference to change color, material, or background while preserving product structure.
- Provide color palette references for accurate color matching.
- Describe lighting as "soft studio lighting" or "natural window light" for catalog consistency.
- Pro excels at ID/face consistency for model photography and natural realism of face/lighting.

### Marketing & Advertising
- Reuse same product/character/brand elements across multiple ad scenes using multi-image fusion.
- Use layer separation to create reusable asset libraries from finished posters.
- Leverage high-density information visualization for campaign posters with text, products, and promotional details.
- Style consistency across variants is a key strength.

### Portrait Retouching
- Provide the original portrait and describe desired changes (outfit, hair, background, lighting) while identity is preserved.
- Natural skin texture is a strength — prompts can specify "natural matte skin texture" for realistic results.
- Use local editing (point/box selection) for targeted retouching rather than regenerating the entire image.
- Good for headshots, profile photos, styling, outfit/hair changes, skin and lighting cleanup.

### Film, Short-Drama & Gaming
- Use layer separation to split character, prop, and background elements for iterative work across shots.
- Leverage cinematic narrative tags (see Style Keywords) for storyboard frames and key visuals.
- Cross-shot consistency is improved — generate character concepts then vary scenes/poses while keeping identity.
- Supports realistic character design for AAA games with coherent lighting between character and environment.

### Presentations & Productivity
- Generate infographics by describing data, layout, and visual hierarchy in detail.
- Support for charts (timeline, bar, pie, line), flowcharts, and annotated diagrams.
- Slides visuals: describe layout zones and content for each zone.
- Small text rendering is improved but may need manual refinement — verify before production use.
- UI mockups are possible (navigation bars, cards, buttons) but pixel-perfect UI is not the strength.

### Social Media & Personal Creative
- Multi-person composites: combine people from separate photos into group photos.
- Outfit transfer: swap clothing between reference images.
- Style transfer/repainting: apply art styles to photos.
- Image restoration: repair or enhance old/damaged photos.
- Sticker packs: generate consistent character stickers with transparent backgrounds (use PNG output).

---

## Seedance Integration (Trusted Input Workflow)

Images generated by Seedream 5.0 Pro (and Lite) are recognized as **trusted inputs** by the entire Seedance video generation family (2.5, 2.0, Fast, Mini). This means:

- The images carry an invisible trusted watermark.
- Seedance models automatically bypass real-person detection / input content moderation for these images.
- **Text-to-Image outputs are trusted automatically** for all customers.
- **Image-to-Image outputs require KYC verification** on the account to be trusted.
- Trust applies only when the image triggers face detection in Seedance.
- Trust exempts **input** moderation only — the final video output is still subject to content moderation.
- Trust applies only to images generated through the **same account's API**.

This makes the T2I → I2V workflow smoother: generate portraits/characters/scenes with Seedream, then feed them directly into Seedance for video without moderation blocks.

---

## Best Practices & Tips for Stable Output

1. **Break complex tasks into steps.** For multi-element composition or fine editing, generate a base image first, then apply local edits iteratively. This dramatically improves controllability.

2. **Be specific about materials.** Describe surfaces precisely: "matte ceramic," "brushed brass," "flowing silk," "weathered wood," "frosted glass." Seedream 5.0 Pro excels at material rendering when given clear direction.

3. **Specify lighting direction and quality.** "Soft backlight creating rim lighting," "harsh midday sun casting sharp shadows," "diffused overcast daylight" — lighting descriptions significantly impact realism.

4. **Reference image order matters.** When passing multiple images, they are referenced as Figure 1, Figure 2, etc., in order. Be explicit about which figure is which.

5. **Use the right resolution.** Start with 1K for rapid iteration; move to 2K for final outputs. The 1.5K tier ($0.045) covers most common sizes up to ~2048×1152.

6. **Layer separation for production.** When creating assets that will be further edited (posters, composites, marketing materials), use layer separation to get independently editable PNG layers with alpha channels.

7. **PNG for transparency.** When you need transparent backgrounds or layer outputs, always set `output_format: "png"`.

8. **Color accuracy.** Provide Hex codes or color palette reference images when precise color matching matters (branding, e-commerce colorways).

9. **First reference image is free.** Optimize your workflow to pass the primary subject as the first image and additional references as subsequent images to minimize cost.

10. **Validate text.** For designs with text (posters, infographics, UI), always proofread generated text — while significantly improved, it may still have occasional errors.

---

## Capability Boundaries

**What Seedream 5.0 Pro is NOT suited for:**
- Fully replacing the final judgment of professional designers
- Highly complex layouts requiring pixel-perfect typographic control
- Generating non-compliant, infringing, or unauthorized content
- UI design requiring pixel-level precision (better to use design tools for final polish)
- 4K output — Pro maxes at ~2.7K on the long edge (16:9). For 4K, use Seedream 5.0 Lite.
- Streaming/progressive output — Pro processes in parallel, returns final result only.
- More than 10 reference images — use Lite for up to 14.
- Real-time interactive applications (no streaming support).

**Known limitations:**
- Small text in information-dense layouts may show occasional instability or typos. Manual refinement recommended.
- Pro is slower than Lite due to higher quality/controllability targets.
- Layer separation count (N) is determined by the prompt and cannot be manually capped.
- Sequential image generation (auto-refinement loops) is not supported.

---

## Code Examples

### cURL — Text-to-Image

```bash
curl https://ark.ap-southeast.bytepluses.com/api/v3/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -d '{
    "model": "dola-seedream-5-0-pro-260628",
    "prompt": "A cinematic portrait of a woman at golden hour on a coastal cliff, soft warm backlight, wind in her hair, photorealistic, 85mm lens, film grain",
    "size": "2K",
    "output_format": "jpeg",
    "watermark": false
  }'
```

### cURL — Image-to-Image (Single Reference)

```bash
curl https://ark.ap-southeast.bytepluses.com/api/v3/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -d '{
    "model": "dola-seedream-5-0-pro-260628",
    "prompt": "Change the background to a snowy mountain landscape at sunset. Keep the person exactly as-is.",
    "image": ["https://example.com/portrait.jpg"],
    "size": "2048x2048",
    "output_format": "png",
    "watermark": false
  }'
```

### cURL — Multi-Reference Image Editing

```bash
curl https://ark.ap-southeast.bytepluses.com/api/v3/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -d '{
    "model": "dola-seedream-5-0-pro-260628",
    "prompt": "Replace the clothing in Figure 1 with the clothing from Figure 2. Preserve the person'\''s identity, pose, and facial expression exactly.",
    "image": [
      "https://example.com/person.jpg",
      "https://example.com/outfit.jpg"
    ],
    "size": "2K",
    "output_format": "png",
    "watermark": false
  }'
```

### Python SDK

```python
import os
# Install: pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark

client = Ark(
    base_url="https://ark.ap-southeast.bytepluses.com/api/v3",
    api_key=os.getenv("ARK_API_KEY"),
)

# Text-to-image
response = client.images.generate(
    model="dola-seedream-5-0-pro-260628",
    prompt="A premium skincare bottle on a marble pedestal, soft studio lighting, commercial product photography",
    size="2K",
    output_format="png",
    response_format="url",
    watermark=False,
)

print(response.data[0].url)
print(f"Input images: {response.usage.input_images}")

# Multi-reference editing
response = client.images.generate(
    model="dola-seedream-5-0-pro-260628",
    prompt="Using the material from Image 1 and color from Image 2, modify the sofa in Image 3 to be deep blue velvet.",
    image=[
        "https://example.com/material.jpg",
        "https://example.com/color-swatch.jpg",
        "https://example.com/room.jpg",
    ],
    size="2K",
    output_format="png",
    response_format="url",
    watermark=False,
)
print(response.data[0].url)
```

### Response Format

```json
{
  "model": "dola-seedream-5-0-pro-260628",
  "created": 1234567890,
  "data": [
    {
      "url": "https://...",
      "output_format": "jpeg",
      "size": "1024x1024"
    }
  ],
  "usage": {
    "input_images": 3,
    "generated_images": 1,
    "output_tokens": 4096,
    "total_tokens": 4096
  }
}
```

---

## Benchmarks & Competitive Position

| Benchmark | Rank |
|-----------|------|
| LMArena Multi-Image Edit | **#2 globally** (1415 pts) — huge leap from Seedream 4.5's #11 |
| LMArena Single-Image Edit | **#4 globally** — ahead of Gemini 3.1 Flash Image (Nano Banana 2) and Gemini 3 Pro Image (Nano Pro) |
| LMArena Text-to-Image | **#6** (scores still converging) |

Internal cross-industry testing shows Pro performs slightly ahead of Nano Banana 2 overall, with strongest advantages in:
- **E-commerce**: +10.17% GSB vs Nano Banana 2
- **Marketing**: +20.20% GSB vs Nano Banana 2
- **Office/productivity**: +3.33% GSB vs Nano Banana 2

---

## Key FAQ Summary

**Q: Does Pro support 4K?** No. Pro is 2K class (up to ~2.7K long edge at 16:9). For 4K, use Seedream 5.0 Lite.

**Q: How many reference images?** Up to 10. First is free; each additional is $0.003.

**Q: Is streaming supported?** No. Pro uses parallel processing and returns the final result.

**Q: Can Pro images feed directly into Seedance?** Yes. T2I outputs are automatically trusted; I2I outputs need KYC.

**Q: Is Pro faster than Lite?** No. Pro targets higher quality and controllability at the cost of speed. No streaming.

**Q: What about text rendering in images?** Significantly improved across 15 languages, but describe as "improved" not "perfect" — typos can still occur with small/dense text.

**Q: What does layer separation output?** 2–20 PNG files with alpha/transparency channels: one background layer plus N element layers. Billed per image.