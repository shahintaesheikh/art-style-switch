"""Seedream keyframe prompt (plan §10.1) and Seedance video prompt (plan §11.1)."""

KEYFRAME_PROMPT = """\
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
"""

SEEDANCE_PROMPT = """\
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
"""
