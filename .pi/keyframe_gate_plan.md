# Keyframe QC Gate — Implementation Plan (`qc_gate.py` v0.1.0)

A two-agent Managed Agents quality gate that sits between styled-keyframe generation and Seedance video generation for style-transfer video pipelines (Unity hand-drawn, anime↔realism, etc.). It scores styled keyframes on a 5-dimension rubric, loops through revision up to 3 attempts, and either hands an approved KF set to Seedance or fails with a structured QC report — never proceeds silently with bad frames.

---

## 1. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ qc_gate.py (driver, local)                                              │
│                                                                         │
│  [round-0] ffmpeg extract  →  Seedream i2i × 3 (parallel)               │
│                               ↓                                         │
│  ┌─ evaluate loop (max 3 cycles) ─────────────────────────────────────┐ │
│  │                                                                    │ │
│  │  Evaluator session (fresh) ── scores ──→ pass? ──yes─→ keyframes   │ │
│  │       ↑ (vision model)                  │             .json +      │ │
│  │       │                                  no            Seedance     │ │
│  │       │                                  ↓                            │
│  │                            Reviser session (fresh)                 │ │
│  │                            (3 custom tools + attached              │ │
│  │                             Seedream prompt-eng. Skill)            │ │
│  │                               │                                    │ │
│  │                 extract_keyframe, generate_keyframe,               │ │
│  │                 submit_revision                                    │ │
│  │                               ↓                                    │ │
│  │                            tool events → driver executes locally   │ │
│  │                               ↓                                    │ │
│  │                            submit_revision → new KF set            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  fail (3 cycles exhausted / infra error) → structured QC report         │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Evaluator**: vision-capable agent (doubao-seed-2-1-pro-260628), ZERO custom tools, NO built-in tools. Produces strict JSON scores per anchor per rubric dimension.
- **Reviser**: same model, 3 custom tools only (`extract_keyframe`, `generate_keyframe`, `submit_revision`), NO built-in tools, Seedream prompt-engineering Skill attached as a ModelArk custom Skill. Emits tool calls; driver executes them.
- **Driver**: single-file Python script, owns all network calls (Managed Agents REST API, Seedream i2i, Assets OpenAPI for asset:// resolution), all ffmpeg/ffprobe invocations, polling loop, cap enforcement, failure handling, reporting.

---

## 2. Core decisions (locked by grill-me interview)

| # | Decision | Locked choice |
|---|----------|---------------|
| Q1–Q4 | Gate topology | Two Managed Agents (Evaluator + Reviser) in ap-southeast; driver orchestrates deterministic evaluate/revise loop |
| Q5 | Failure policy | 3 outer-cycle hard cap; fail-open halt with best-so-far + full QC report; never silently calls Seedance |
| Q6 | Hosting | HiAgent/Managed Agents (ap-southeast); thin Python polling driver; no MCP server, no ECS, no tunnel |
| Q7 | Reviser toolset | 3 custom tools only: extract_keyframe, generate_keyframe, submit_revision. refine_seedream_prompt removed (Q33) |
| Q8 | Cross-anchor coherence | Approved KF URLs injected into every Evaluator/Reviser prompt for visual comparison |
| Q9 | Image passing | Seedream `response_format:"url"` (ModelArk CDN, ~24h TTL) as default; URL-passthrough for `user.message` images; base64 JPEG in `custom_tool_result` preview blocks; Files API only as out-of-band fallback |
| Q10 | Model | doubao-seed-2-1-pro-260628, standard speed, both agents |
| Q11 | Custom-tools delivery | Declarative `type:"custom"` tools; driver = "business-side"; polling event loop (no SSE) |
| Q12 | Event consumption | 2s cursor-based polling on `/api/v3/sessions/{id}/events?after=<last_event_id>` |
| Q13 | Terminator | Explicit `submit_revision` custom tool carrying explicit delta payload; driver never parses free-text "DONE" |
| Q14 | Evaluator sessions | Fresh session per round, all KFs in a single prompt |
| Q15 | Session lifetime | Fresh Evaluator session per round, fresh Reviser session per round (no long sessions); driver builds compact failure-history summary between rounds; your context-compaction skill is a v2 improvement |
| Q16 | Rubric | 5 dimensions (see §3); hard gates geometry+composition =5 min; soft gates medium+palette+line_quality ≥4 min; medium generalized for any art style (pencil, anime, oil, photorealism, watercolor, etc.) |
| Q17 | Style authority | `style_image_url` and `style_prompt` both optional driver inputs; image is sole authority when provided; prompt used only when no image |
| Q18 | Image transport | response_format="url" default; base64 inline fallback; Files API for >24h artifacts; zero object storage |
| Q19 | Environment | Dedicated minimal sandboxed Environment per pipeline; ALL built-in tools disabled (no bash, web_fetch, code_interpreter); no vault; no MCP servers; agents can ONLY call declared custom tools. Built-in tools can be enabled later for skills that need scripts |
| Q20 | Evaluator output schema | Strict driver-actionable JSON (see §4); driver recomputes pass/fail from thresholds; schema-retry nudge on parse failure |
| Q21 | submit_revision payload | Explicit delta `{regenerated:[{anchor_id,timestamp_sec,keyframe_url,prompt_used,seed,changes_made}], unchanged_approved_anchor_ids, attempts_used, notes}`; no `remaining_concerns` field |
| Q22 | Per-session caps | 8 LLM turns, 3 `generate_keyframe` calls per anchor, 6 total KF calls, 4-minute wall clock; driver force-closes + auto-builds delta on cap hit, tagged `driver_forced:true` |
| Q23 | Evaluator rescope | Round 1 scores all anchors; rounds ≥2 score only newly-regenerated anchors, with approved anchors shown as image-context marked `[previously approved — shown only for cross-anchor coherence, do NOT rescore]`; cross-anchor coherence notes every round |
| Q24 | generate_keyframe surface | `{anchor_id, timestamp_sec, prompt, negative_prompt, force_new_seed}`; model/aspect/strength/guidance/steps driver-pinned; seeds round-trip deterministically unless force_new_seed=true |
| Q25 | refine_seedream_prompt | REMOVED as a tool — superseded by Q33 (attached ModelArk Skill) |
| Q26 | Round-0 bootstrap | Driver owns initial KF generation: extracts raw frames via ffmpeg, runs Seedream once per anchor with driver-pinned conservative seed prompt + geometry-preserving negative prompt, parallel via thread pool; gate is self-contained |
| Q27 | Fail-open behavior | Halt + return `{status:"failed", reason:"attempts_exhausted", best_keyframes, qc_report}`, exit code 2; Seedance never called on failure |
| Q28 | Infrastructure errors | Simple retry-once after 5s wait for retryable errors (5xx, 429, network timeout, malformed JSON); 4xx (except 429) and ffmpeg failures fail fast with `{status:"infrastructure_error", stage, error}`, exit code 3; infra errors do NOT consume QC attempts |
| Q29 | Reporting | Single compact JSON line on stdout (pipeline-friendly); pretty-printed full QC report via `--report-path`; no --verbose flag in v1 |
| Q30 | Deliverable shape | Single-file Python script `qc_gate.py`, prompts as module-level constants; `requests` + Pillow deps; system ffmpeg/ffprobe on PATH; exit code 0=passed, 2=QC failed, 3=infra error |
| Q31 | Agent lifecycle | Self-bootstrapping; driver auto-creates Evaluator + Reviser agents + minimal Environment on first run; caches IDs + prompt-hash in `~/.config/kf-qc/agents.json`; auto-updates agents when driver version changes; `--evaluator-agent-id` / `--reviser-agent-id` overrides available |
| Q32 | Prompt-engineering skill v1 | Your authored skill already exists; wired in day one |
| Q33 | Skill integration | Your authored Seedream prompt-engineering skill is a pure knowledge pack (SKILL.md); uploaded via `POST /api/v3/skills` zip upload, attached to Reviser as `skills:[{type:"custom", skill_id, version}]`; toolset collapses to 3 tools |
| Q34 | Input schemes | `--raw-video` (required) accepts local path / https:// URL / asset:// URI; `--style-image` (optional) accepts the same three; `--style-prompt` (optional) fallback; at least one of style-image/prompt required; 500 MB download cap; temp file cleanup |
| Q35 | Region | ap-southeast hardcoded; inference at `https://ark.ap-southeast.bytepluses.com/api/v3`; Assets OpenAPI at `https://ark.ap-southeast-1.byteplusapi.com`; cn-beijing not supported in v1 |
| Q36 | Model ID | doubao-seed-2-1-pro-260628, standard speed, both agents |
| Q37 | Inter-round history | Driver-built deterministic JSON summary block (~100–300 tokens per round, built from Evaluator rationales + Reviser changes_made + driver tool audit); no LLM compaction |
| Q38 | Visual layout in prompts | Style reference (image or text) → labeled raw frames per anchor (geometry GT) → labeled styled KFs per anchor tagged [NEW] or [previously approved] → rubric/thresholds/schema/history/instructions last |
| Q39 | Image + parallelism | URL-passthrough in `user.message`; base64 JPEG in `custom_tool_result` preview images; round-0 Seedream calls parallel via thread pool; Reviser-session generate_keyframe calls serialized; Seedream sanity-checked (HTTP 200, decodes via Pillow, ±5% of 3:4); ffmpeg extracts JPEG -q:v 2, no letterbox |
| Q40 | Credentials | `ARK_API_KEY` env var (or `--ark-api-key`), required; `ARK_ACCESS_KEY`/`ARK_SECRET_KEY` env vars (or CLI overrides), only required when asset:// inputs used; no config-file secrets |
| Q41 | Polling | 2s cursor-based polling; idle+completed on Reviser = driver-forced submit; terminated = infra error; 4-minute polling timeout; 1s post-tool-result wait |
| Q42 | Anchors | Fixed 3 anchors (A0/A1/A2) for v1; defaults to 0%/33%/66% of video duration via ffprobe; `--anchor-timestamps t0,t1,t2` must be exactly 3 floats; N-anchor deferred to v2 |
| Q43 | Dependencies | Python 3.10+, `requests`, Pillow, system ffmpeg/ffprobe (with `--ffmpeg-path`/`--ffprobe-path` overrides); stdlib for everything else; stdlib argparse |
| Q44 | Output KFs JSON | Anchor-keyed metadata: `{status, raw_video, style_image, model, generator_version, aspect_ratio, keyframes:[{anchor_id,timestamp_sec,image_url,prompt_used,negative_prompt_used,seed}], qc_report_path, qc_rounds_taken}`; pipeline owns the Seedance call |
| Q45 | Prompts | Module-level string constants in `qc_gate.py` (EVALUATOR_SYSTEM_PROMPT, REVISER_SYSTEM_PROMPT, ROUND0_SEED_PROMPT, ROUND0_NEGATIVE_PROMPT); versioned with driver |
| Q46 | Temp files | One `tempfile.TemporaryDirectory` per run, cleaned up via context manager in try/finally; only `--report-path` and `--output-kfs-json` persist |
| Q47 | Wrap-up defaults | Silent (no verbose flag), Python 3.10+, stdlib argparse, version `0.1.0`, v1 does NOT call Seedance (pipeline owns it), attached Skill via `--seedream-prompt-skill-id`, standard header comment, all tunables as UPPER_SNAKE_CASE constants |

---

## 3. Rubric (5 dimensions, generalized across art styles)

| Dimension | Type | Min score | What it measures |
|-----------|------|-----------|------------------|
| `geometry` | Hard gate | **5** | Object count, positions, orientations, vanishing point matches raw frame 1:1 |
| `composition` | Hard gate | **5** | Same framing/zoom/crop as raw frame, 3:4 aspect, no letterbox |
| `medium` | Soft gate | **4** | Art-style fidelity matches target (pencil+colored-pencil / anime cel / photorealism / oil / watercolor / etc.) — must match target medium, not drift |
| `palette` | Soft gate | **4** | Color treatment matches target (hatched crimson vs flat red; graphite-on-cream vs saturated color; etc.) |
| `line_quality` | Soft gate | **4** | Mark/edge character matches target (sketchy graphite for pencil, clean cel lines for anime, painterly edges for oil, etc.) |

Scoring scale 1–5 (1 = completely wrong, 5 = pixel-perfect match on that dimension). Driver recomputes pass/fail from thresholds rather than trusting the model's `pass` boolean (but the boolean is included for debug).

---

## 4. Evaluator output schema (strict)

Driver retries once with a "your previous response wasn't valid JSON following the schema" nudge if parsing fails.

```json
{
  "pass": true,
  "round": 1,
  "keyframes": [
    {
      "anchor_id": "A0",
      "timestamp_sec": 0,
      "image_url": "https://…",
      "scores": {
        "medium":       {"score": 5, "rationale": "…"},
        "palette":      {"score": 4, "rationale": "…"},
        "line_quality": {"score": 5, "rationale": "…"},
        "geometry":     {"score": 5, "rationale": "…"},
        "composition":  {"score": 5, "rationale": "…"}
      },
      "pass": true,
      "failed_dimensions": [],
      "suggested_focus": "concrete fix hint for the Reviser"
    }
  ],
  "cross_anchor_coherence_notes": "A1 palette drifts cooler than approved A0; bring warm cream tone back",
  "summary": "1-sentence overall verdict"
}
```

- Round 1: all 3 anchors scored, all 5 dimensions.
- Rounds ≥2: only anchors tagged `[NEW]` are scored; `[previously approved]` anchors are NOT rescored but are visible for coherence.
- `image_url` is echoed back from the driver-provided prompt for traceability.

---

## 5. Reviser custom tool schemas (input_schema, JSON Schema, additionalProperties=false)

All three tools are `type:"custom"`, declared at agent creation. Built-in toolset is absent from the agent definition entirely.

### 5.1 `extract_keyframe`
Re-extracts a raw frame at a given timestamp (driver runs ffmpeg). Used when the Reviser decides to shift an anchor's timestamp slightly (Q4: "can change which raw keyframe timestamps are used"). Cannot add/remove anchors.
```json
{
  "anchor_id": {"type":"string","enum":["A0","A1","A2"]},
  "timestamp_sec": {"type":"number","minimum":0}
}
```
Returns: `{ok:true, anchor_id, timestamp_sec, image_as_base64_jpeg_meta_only}` — driver delivers the frame as a `{type:"image", source:{type:"base64", media_type:"image/jpeg", data}}` content block in the tool result.

### 5.2 `generate_keyframe`
Calls Seedream i2i using the pinned constants (aspect/strength/guidance/steps/model) and the provided prompt/negative_prompt; reuses prior seed for that anchor unless `force_new_seed:true`.
```json
{
  "anchor_id": {"type":"string","enum":["A0","A1","A2"]},
  "timestamp_sec": {"type":"number","minimum":0},
  "prompt": {"type":"string","minLength":1,"maxLength":4000},
  "negative_prompt": {"type":"string","maxLength":4000},
  "force_new_seed": {"type":"boolean"}
}
```
Returns: `{ok:true, anchor_id, timestamp_sec, keyframe_url:"https://…", seed, prompt_used, negative_prompt_used, model:"doubao-seedream-5-0-lite-260128"}` as text, plus a `{type:"image"}` content block (base64 JPEG) showing the generated KF so the Reviser can see its own output.

### 5.3 `submit_revision`
Terminator. Driver validates that every `anchor_id` in `regenerated` was a failing anchor in the prior Evaluator round (sanity guard against accidental re-encoding of approved KFs), merges with unchanged approved anchors, and advances to the next Evaluator round.
```json
{
  "regenerated": {
    "type":"array",
    "items": {
      "type":"object",
      "properties": {
        "anchor_id": {"type":"string","enum":["A0","A1","A2"]},
        "timestamp_sec": {"type":"number","minimum":0},
        "keyframe_url": {"type":"string","format":"uri"},
        "prompt_used": {"type":"string"},
        "seed": {"type":"integer"},
        "changes_made": {"type":"string","maxLength":1000}
      },
      "required": ["anchor_id","timestamp_sec","keyframe_url","prompt_used","seed","changes_made"],
      "additionalProperties": false
    }
  },
  "unchanged_approved_anchor_ids": {
    "type":"array",
    "items": {"type":"string","enum":["A0","A1","A2"]}
  },
  "attempts_used": {"type":"integer","minimum":1},
  "notes": {"type":"string","maxLength":2000}
}
```

---

## 6. Driver state machine

```
                ┌──────────────┐
                │   START      │
                └──────┬───────┘
                       ↓
        ┌──────────────────────────────┐
        │ Parse CLI, resolve inputs    │
        │ (local/https/asset://),      │
        │ load or bootstrap agents     │
        └──────────────┬───────────────┘
                       ↓
        ┌──────────────────────────────┐
        │ Round 0: ffmpeg extract raw  │
        │ frames, then Seedream i2i ×3 │
        │ (parallel) → initial KFs     │
        └──────────────┬───────────────┘
                       ↓
        ┌──────────────────────────────┐
   ┌──→ │ EVALUATE round (fresh sess.) │
   │    │ Send visual layout (§Q38)    │
   │    │ Poll 2s until idle           │
   │    │ Parse JSON, recompute pass   │
   │    └──────────┬───────────────────┘
   │               ↓
   │         ┌──────────┐
   │    pass │          │ fail (and attempts < 3)
   │         ↓          ↓
   │  ┌──────────┐ ┌──────────────────────────┐
   │  │ SUCCESS  │ │ REVISE (fresh session)   │
   │  │ write    │ │ Inject history + failures│
   │  │ output   │ │ Poll 2s, handle 3 tools │
   │  │ KFs JSON │ │ Enforce per-session caps │
   │  │ + report │ │ On submit_revision (or   │
   │  │ exit 0   │ │  driver-force), merge    │
   │  └──────────┘ │ KFs → next EVALUATE round│
   │               │ attempts++               │
   │               └──────────────┬───────────┘
   │                              │
   │ fail (attempts ≥ 3 or hard gate unresolvable)
   │               ┌──────────────↓───────────┐
   │               │ FAIL (qc-failed)         │
   │               │ write best KFs + report  │
   │               │ exit 2                   │
   │               └──────────────────────────┘
   │  infra error at any stage
   └─── (retry once) → still failing? ─→ INFRA FAIL: write report, exit 3
```

---

## 7. CLI flags

| Flag | Required | Default | Notes |
|------|----------|---------|-------|
| `--raw-video` | yes | — | local path / https:// URL / asset:// URI |
| `--style-image` | no* | — | same three schemes |
| `--style-prompt` | no* | — | used only if no style image |
| `--anchor-timestamps` | no | 0%,33%,66% of duration | exactly 3 comma-separated floats (seconds) |
| `--ark-api-key` | no | `$ARK_API_KEY` | |
| `--ark-access-key` | no | `$ARK_ACCESS_KEY` | only needed for asset:// |
| `--ark-secret-key` | no | `$ARK_SECRET_KEY` | only needed for asset:// |
| `--region` | hidden | `ap-southeast` | no flag in v1 (Q35) |
| `--evaluator-agent-id` | no | auto-bootstrapped | cached in `~/.config/kf-qc/agents.json` |
| `--reviser-agent-id` | no | auto-bootstrapped | |
| `--seedream-prompt-skill-id` | no | none | ModelArk skill ID for your authored Seedream prompt-engineering skill; attached to Reviser if provided |
| `--environment-id` | no | auto-bootstrapped | |
| `--report-path` | no | none (stdout only) | writes pretty JSON to this path |
| `--output-kfs-json` | yes | — | Seedance-ready keyframes JSON (written only on pass) |
| `--ffmpeg-path` | no | `ffmpeg` on PATH | |
| `--ffprobe-path` | no | `ffprobe` on PATH | |

\*At least one of `--style-image` / `--style-prompt` is required.

---

## 8. Constants (UPPER_SNAKE_CASE, top of script)

```python
VERSION = "0.1.0"
MODEL_ID = "doubao-seed-2-1-pro-260628"
SEEDREAM_MODEL_ID = "doubao-seedream-5-0-lite-260128"
ASPECT_RATIO = "3:4"
ASPECT_W, ASPECT_H = 3, 4
SEEDREAM_RESPONSE_FORMAT = "url"
INFERENCE_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
ASSETS_OPENAPI_HOST = "ark.ap-southeast-1.byteplusapi.com"
AGENTS_CACHE_PATH = "~/.config/kf-qc/agents.json"
MAX_QC_ATTEMPTS = 3
REVISER_MAX_TURNS = 8
REVISER_MAX_KF_PER_ANCHOR = 3
REVISER_MAX_KF_TOTAL = 6
REVISER_WALL_CLOCK_SEC = 240
POLL_INTERVAL_SEC = 2
POLL_TIMEOUT_SEC = 240
POST_TOOL_WAIT_SEC = 1
INFRA_RETRY_WAIT_SEC = 5
INFRA_MAX_RETRIES = 1
DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024
ASPECT_TOLERANCE = 0.05
FFMPEG_QV = 2
```

Round-0 seed prompt (driver-pinned, conservative):
```
Apply the provided style reference to this raw video frame to produce a styled keyframe. Preserve the exact geometry, object count, positions, orientations, camera angle, and vanishing point of every element in the original frame 1:1. Do not add, remove, move, rotate, resize, or morph any object. Maintain the same framing, zoom level, and 3:4 aspect ratio as the original — no letterboxing, no cropping. Match the style reference's medium, palette, and line_quality faithfully.
```

Round-0 negative prompt:
```
morphing, distorted geometry, extra objects, missing objects, rotated objects, changed object count, changed orientation, shifted vanishing point, letterboxing, watermark, cropped, zoomed in, zoomed out, wrong aspect ratio, blurry, low resolution
```

---

## 9. QC report schema (written to `--report-path`, single-line compact version on stdout)

```json
{
  "status": "passed" | "failed" | "infrastructure_error",
  "version": {"qc_gate": "0.1.0", "evaluator_agent": "agt-xxx@v2", "reviser_agent": "agt-yyy@v3", "prompt_skill": "skill-zzz@1"},
  "video": "<raw_video_path_or_url>",
  "style": {"image_url": "…" | null, "prompt": "…" | null},
  "anchor_timestamps_sec": [0.0, 2.0, 5.0],
  "final_keyframes": [
    {"anchor_id":"A0","timestamp_sec":0.0,"keyframe_url":"https://…","prompt_used":"…","negative_prompt_used":"…","seed":123}
  ],
  "seedance_input_ready": true,
  "rounds": [
    {
      "round": 1,
      "evaluator_session_id": "sesn-…",
      "scores_per_kf": {"A0":{…},"A1":{…},"A2":{…}},
      "pass": false,
      "failures": [{"anchor_id":"A1","failed_dimensions":["medium","palette"],"suggested_focus":"…"}],
      "cross_anchor_coherence_notes": "…",
      "reviser_session_id": "sesn-…" | null,
      "reviser_actions": [
        {"tool":"extract_keyframe","input":{…}},
        {"tool":"generate_keyframe","input":{…},"result":{"keyframe_url":"…","seed":456}},
        {"tool":"submit_revision","input":{…},"driver_forced":false}
      ],
      "driver_forced": false
    }
  ],
  "failure_reason": "attempts_exhausted" | null,
  "infrastructure_error": {"stage":"…","error":"…"} | null,
  "duration_seconds": 87.4,
  "total_kf_generations": 7
}
```

---

## 10. Self-bootstrapping flow (Q31)

On startup, before the round-0 Seedream calls:

1. Expand `~/.config/kf-qc/agents.json`.
2. If file exists and `prompt_hash` matches `hashlib.sha256(EVALUATOR_SYSTEM_PROMPT + REVISER_SYSTEM_PROMPT + VERSION).hexdigest()`, reuse cached agent IDs and environment ID.
3. Otherwise:
   a. `POST /api/v3/environments` — create minimal sandboxed env (cloud, networking: unrestricted if needed for API access? TBD at impl; no packages, no env vars, no vaults).
   b. `POST /api/v3/agents` for Evaluator — model = `{id: MODEL_ID, speed:"standard"}`, system = EVALUATOR_SYSTEM_PROMPT, NO tools, NO skills, NO mcp_servers, NO multiagent.
   c. `POST /api/v3/agents` for Reviser — same model, system = REVISER_SYSTEM_PROMPT, tools = 3 custom tool declarations (with input_schema per §5), skills = `[{type:"custom", skill_id: <from --seedream-prompt-skill-id if provided>}]` (if flag is set; otherwise empty skills array), NO built-in toolset entry (entire `tools` array is the 3 `type:"custom"` entries), NO mcp_servers, NO multiagent.
   d. Write `{evaluator_agent_id, reviser_agent_id, environment_id, evaluator_version, reviser_version, prompt_hash, version}` to cache.
4. If user passed explicit `--evaluator-agent-id` / `--reviser-agent-id` / `--environment-id`, those override the cache and no bootstrap happens.
5. If the driver detects the on-disk `VERSION` has changed (and thus prompt_hash differs), it `PUT`s updated prompts to the agents (passing the current `version` integer) and refreshes the cache.

---

## 11. Per-round user-message shapes

### 11.1 Evaluator (round 1)
Content blocks in order:
1. Text: "## Style reference (the art style the final keyframes MUST match):"
2. Image (style reference) OR text block with style prompt
3. Text: "## Raw video frames at anchor timestamps (geometry/composition ground truth — cars, buildings, framing, vanishing point must match these exactly):"
4. For each anchor: labeled text "### Raw frame at t=<N>s (<anchor_id>):" then raw frame image
5. Text: "## Styled keyframes to score (apply the rubric below to each; compare geometry/composition against the raw frames above; compare medium/palette/line_quality against the style reference above):"
6. For each anchor: labeled text "### Styled keyframe at t=<N>s (<anchor_id>): [NEW in this round — score all 5 dimensions]" then styled KF image
7. Text: rubric definition, thresholds, JSON schema, output instruction ("Return exactly one JSON object matching the schema; no prose outside the JSON").

### 11.2 Evaluator (rounds ≥2)
Same blocks as round 1, but:
- Block 6 labels approved anchors as `[previously approved — shown only for cross-anchor coherence, do NOT rescore]` and newly-regenerated anchors as `[NEW in this round — score all 5 dimensions]`.
- New block inserted before block 7: "## Prior rounds summary\n<driver-built compact failure-history JSON block (Q37)>".

### 11.3 Reviser
Content blocks in order:
1. Text: "## Style reference:" + image or prompt
2. Text: "## Raw video frames at current anchor timestamps:" + raw frame images per anchor
3. Text: "## Approved styled keyframes (do NOT modify these, use as coherence reference):" + approved KF images per anchor (tagged [approved])
4. Text: "## Failing styled keyframes from the previous Evaluator round (fix these):" + failing KF images per anchor (tagged [needs fix])
5. Text: "## Evaluator feedback for failing anchors:" + per-anchor scores (only failed dimensions), rationales, and `suggested_focus`
6. Text: "## Prior revisions attempted:" + driver-built compact failure-history block (Q37)
7. Text: "## Your task\n…instructions, available tools (3), per-session caps reminder, instruction to call submit_revision when done…"
8. If `--seedream-prompt-skill-id` was supplied, the ModelArk Skill is attached to the agent and its SKILL.md is loaded automatically by the Managed Agents runtime — no need to inline.

---

## 12. Custom tool result shapes (driver → agent)

After executing a tool, the driver POSTs to `/api/v3/sessions/{id}/events` with:

```json
{
  "events": [{
    "type": "user.custom_tool_result",
    "custom_tool_use_id": "<evt-id-from-agent>",
    "is_error": false,
    "content": [
      {"type": "text", "text": "{...json result...}"},
      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "<base64 JPEG>"}}
    ]
  }]
}
```

- `extract_keyframe`: text = `{ok:true,anchor_id,timestamp_sec}`, image = extracted raw frame.
- `generate_keyframe`: text = `{ok:true,anchor_id,timestamp_sec,keyframe_url,seed,prompt_used,negative_prompt_used,model}`, image = downloaded Seedream JPEG re-encoded to base64.
- `submit_revision`: no tool result needed — this ends the session. After receiving the tool call, driver validates payload, ends polling, and proceeds to the next Evaluator round.
- On tool execution error: `is_error:true`, content = one text block `{ok:false,error:"…"}`.

---

## 13. Implementation file layout

v1 deliverable is a single file at the repo/project root:

```
qc_gate.py          # main driver (argparse, state machine, API clients, polling loop,
                    #              ffmpeg/ffprobe wrappers, Seedream client, asset:// resolver,
                    #              agent bootstrap, prompt constants, rubric thresholds,
                    #              per-session cap enforcement, report writer)
```

Helper functions (in same file, single file for v1):

- `resolve_input(uri_or_path) -> Path` — handles local/https/asset://, returns local temp path.
- `resolve_asset(asset_id) -> str` — HMAC-SHA256 v4 signed request to Assets OpenAPI to get a downloadable URL.
- `ffprobe_duration(video_path) -> float`
- `ffmpeg_extract_frame(video_path, timestamp_sec, out_path) -> Path`
- `seedream_i2i(raw_frame_path, style_image_path_or_none, style_prompt_or_none, prompt, negative_prompt, seed, force_new_seed) -> dict` — returns `{url, seed, prompt_used, negative_prompt_used}`; uses response_format="url", parallel-safe (uses a thread pool in round-0).
- `download_as_jpeg_b64(url, max_bytes) -> str` — downloads URL via requests, validates with Pillow (decode + aspect ±5%), returns base64 string.
- `bootstrap_agents(args) -> (evaluator_id, reviser_id, env_id)` — creates/reads cache, applies PATCH on version drift.
- `create_session(agent_id, env_id) -> session_id`
- `send_message(session_id, content_blocks) -> None`
- `poll_until_idle(session_id, deadline_epoch) -> list[events]` — 2s cursor polling; returns all new events up to idle/terminated/timeout.
- `run_evaluator_round(round_idx, kfs_state, history, args) -> evaluator_result`
- `run_reviser_round(round_idx, failures, kfs_state, history, args) -> revision_delta`
- `execute_tool_call(name, input, state) -> tool_result`
- `scores_pass(scores_per_kf) -> (bool, failures[])` — applies rubric thresholds.
- `build_failure_history(rounds[]) -> str` — compact driver-built summary per Q37.
- `write_outputs(result, args)` — writes `--output-kfs-json` (on pass) and `--report-path`, prints compact JSON to stdout.

---

## 14. Exit codes

| Exit code | Meaning |
|-----------|---------|
| 0 | Passed — `--output-kfs-json` written, Seedance-ready |
| 2 | QC failed (3 attempts exhausted) — best-so-far KFs + report written |
| 3 | Infrastructure error — report written with `stage` and `error` |

---

## 15. v1 scope boundaries (explicitly out)

- No N-anchor support (fixed A0/A1/A2).
- No configurable region (ap-southeast only).
- No built-in toolset enabled on agents (bash, web_fetch, etc.) — architecture supports flipping this on later for skills with scripts.
- No verbose/streaming logging (silent-until-final-line).
- No automatic Seedance invocation — driver produces `keyframes.json`, pipeline calls Seedance.
- No context-compaction LLM call between rounds (driver-built deterministic summary).
- No MCP servers, no Vault, no ECS, no tunnel, no object storage.
- No web UI or dashboard — CLI only.
- No Mira/Feishu notifications on failure (caller decides).

---

## 16. Implementation order (recommended)

1. **Scaffold**: argparse, constants, input resolver (local/https/asset://), ffmpeg/ffprobe wrappers, temp dir lifecycle.
2. **ModelArk clients**: inference REST client (Bearer), Assets OpenAPI client (HMAC-SHA256 v4), Seedream i2i client (response_format=url, parallel round-0, sanity checks).
3. **Agent bootstrap**: environment create, agent create (Evaluator + Reviser with 3 custom tools, optional Skill attachment), cache file logic, version-drift PATCH.
4. **Session primitives**: create session, send message (URL-passthrough image blocks), cursor-poll loop, send tool result (base64 image blocks), terminated/idle state handling.
5. **Evaluator round**: user-message layout (§11.1/11.2), JSON parsing + schema retry, threshold pass/fail computation, failure structure.
6. **Reviser round**: user-message layout, per-session cap enforcement, tool dispatch (extract, generate, submit), driver-forced submit on cap hit.
7. **Outer loop**: 3-attempt state machine (§6), inter-round history builder, best-so-far tracking.
8. **Reporting**: stdout compact JSON, `--report-path` pretty JSON, `--output-kfs-json` anchor-keyed output, exit codes.
9. **Infra error paths**: retry-once for retryable errors, fast-fail for 4xx/ffmpeg, distinct exit code, stage-tagged error in report.
10. **Smoke test**: run against `unity-5.mp4` + `handDrawnStyle.jpeg` (already on disk from prior pipeline work), verify: round-0 → 1–2 evaluate/revise cycles → pass or fail with clean report → keyframes.json ready for Seedance.
11. **Doc update**: fold QC gate section into `unity_handdrawn_pipeline_vodfree.md` and republish Feishu doc `DMuHdUuUsob7Z3xKcu3mEK3By2e`.

---

## 17. Open questions to resolve during implementation (small, non-architectural)

- **Environment networking**: does the minimal Environment need `networking.type:"unrestricted"` for the agent runtime to fetch `https://` image URLs we pass in `user.message`, or does Managed Agents fetch URLs server-side before model input? Test at impl; if URLs are fetched server-side the env can have no networking.
- **Reviser system prompt specifics**: the exact wording of the Reviser's role/constraints/tool-use instructions (drafted in `qc_gate.py` constants; will be tuned against smoke-test runs).
- **Evaluator system prompt specifics**: rubric wording, score-anchor definitions (what each score 1–5 means per dimension), JSON-only output instruction (same — drafted as constants, tuned against test data).
- **i2i strength default**: the `strength`/style-weight value passed to Seedream that preserves geometry while allowing style transfer; empirically tuned during smoke test against the Unity/hand-drawn assets (the value that avoids the Q2 frame-2 stained-glass regression while still applying pencil style). The constant `SEEDREAM_I2I_STRENGTH` (or equivalent param name) will be set once and pinned.
- **Your authored prompt-engineering Skill ID**: provide at runtime via `--seedream-prompt-skill-id`; no code changes needed.

These are implementation details to resolve during coding/testing, not design decisions that change the architecture.
