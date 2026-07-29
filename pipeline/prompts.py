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
