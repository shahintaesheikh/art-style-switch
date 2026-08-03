# `qc_gate.py` — Worker Implementation Plan

Single-file Python worker that sits between raw video + style reference and a Seedance video-generation call. It runs a Managed-Agents evaluate/revise loop on the styled keyframes so bad frames never reach Seedance.

```
raw video + style ref
        │
        ▼
┌─────────────────────┐
│   qc_gate.py        │
│                     │
│  round-0: ffmpeg →  │  3 Seedream i2i calls in parallel
│  Seedream KF gen    │
│        │            │
│        ▼            │
│  ┌─ loop (≤3) ───┐  │
│  │  Evaluator ──→│ pass ──→ keyframes.json ──→ Seedance
│  │     (agent)   │  │
│  │      │ fail   │  │
│  │      ▼        │  │
│  │  Reviser ──→  │  │
│  │  (agent + 3   │  │
│  │   custom      │  │
│  │   tools)      │  │
│  └───────────────┘  │
│        │ fail after 3×
│        ▼            │
│  QC report + halt   │
└─────────────────────┘
```

## Scope

v0.1.0 does exactly this:

1. Take a raw video (local / https:// / asset://) and a style reference (image in same schemes, or text prompt), extract 3 anchor frames at 0%/33%/66% (or `--anchor-timestamps`), run Seedream i2i per anchor to produce baseline styled keyframes.
2. Send KFs to the **Evaluator** agent (vision model, no tools) with the style ref + raw frames; Evaluator returns a strict JSON scorecard on 5 dimensions.
3. If all anchors pass thresholds → write `keyframes.json`, exit 0. Caller feeds it to Seedance.
4. If any anchor fails → spin up a **Reviser** agent with 3 custom tools (`extract_keyframe`, `generate_keyframe`, `submit_revision`) plus an attached Seedream prompt-engineering Skill; Reviser iterates (extract → refine using Skill → regenerate → inspect) until it calls `submit_revision` or hits per-session caps.
5. Loop back to step 2. After 3 cycles, halt with QC report, exit 2. Never call Seedance on failing frames.

Out of scope for v1: N anchors, cn-beijing region, verbose logging, configurable model/region, automatic Seedance invocation (caller owns Seedance), built-in bash/web tools on agents, MCP servers, vault, object storage.

## Wire-up

The worker is three layers:

**1. API clients (driver-side HTTP)**
- **Inference client** (Bearer `$ARK_API_KEY`, `https://ark.ap-southeast.bytepluses.com/api/v3`): Agents CRUD, Environments, Sessions, Session events (POST + GET polling), Seedream images/generations.
- **Assets OpenAPI client** (AK/SK HMAC-SHA256 v4, `https://ark.ap-southeast-1.byteplusapi.com`): only called when an input is `asset://`; resolves asset IDs to downloadable HTTPS URLs.
- **No SDKs** — raw `requests`; matches the zero-infra pipeline.

**2. Two Managed Agents (self-bootstrapped)**

Auto-created on first run, IDs cached in `~/.config/kf-qc/agents.json` with a prompt-hash so the driver auto-PATCHes agents when its prompts change.

| | Evaluator | Reviser |
|---|---|---|
| Model | `doubao-seed-2-1-pro-260628` | `doubao-seed-2-1-pro-260628` |
| System prompt | Rubric, scoring instructions, strict JSON schema output | Repair goals, tool-use protocol, cap reminders, coherence rules |
| Tools | **none** (vision only) | 3 custom tools (see below) |
| Skills | none | your authored Seedream prompt-engineering Skill (via `--seedream-prompt-skill-id`) |
| Built-in toolset | disabled | disabled (no bash/web/code) |
| Environment | minimal cloud sandbox, no packages, no vault | same |
| Session per round | fresh session | fresh session |

**3. Custom tool implementations (driver executes when Reviser emits `agent.custom_tool_use`)**

| Tool | Input | What driver does | Return to agent |
|---|---|---|---|
| `extract_keyframe` | `{anchor_id, timestamp_sec}` | `ffmpeg -ss <t> -i <video> -frames:v 1 -q:v 2 <tmp>.jpg` | text `{ok, anchor_id, timestamp_sec}` + base64 JPEG image block |
| `generate_keyframe` | `{anchor_id, timestamp_sec, prompt, negative_prompt, force_new_seed}` | Call Seedream i2i (`doubao-seedream-5-0-lite-260128`, `response_format:"url"`, driver-pinned strength/3:4 aspect, reuse prior seed per anchor unless `force_new_seed`); sanity-check image (HTTP 200, Pillow decodes, ±5% of 3:4) | text `{ok, keyframe_url, seed, prompt_used, model}` + base64 JPEG preview |
| `submit_revision` | `{regenerated:[{anchor_id, timestamp_sec, keyframe_url, prompt_used, seed, changes_made}], unchanged_approved_anchor_ids, attempts_used, notes}` | Validate regen anchor_ids were failing in prior round; merge with approved KFs; end Reviser session; advance to next Evaluator round | (terminates session) |

## Event loop (per session)

1. `POST /sessions` → session id (idle).
2. `POST /sessions/{id}/events` with `user.message` (text + `{type:image}` blocks, ordered per layout spec).
3. Poll `GET /sessions/{id}/events?after=<last_id>` every 2s.
   - On `session.status_idle` + `stop_reason.type="requires_action"`: collect every `agent.custom_tool_use` event from the batch, execute locally, POST `user.custom_tool_result` (text + optional image base64 block), wait 1s, resume polling.
   - On `session.status_idle` + `stop_reason.type="completed"` (Evaluator): parse text as JSON scores; one retry with a schema-correction nudge if invalid.
   - On `session.status_idle` + `stop_reason.type="completed"` (Reviser): treat as driver-forced submit (take latest generated KF per touched anchor), tagged `driver_forced:"no_submit_revision"`.
   - On `session.status_terminated` or 4-minute poll timeout: infrastructure error (retry once after 5s, then exit 3).

## Rubric & thresholds (applied by driver, not trusted from model)

| Dimension | Min | Type | Measures |
|---|---|---|---|
| `geometry` | 5 | hard | object count/positions/orientations/vanishing point match raw frame 1:1 |
| `composition` | 5 | hard | same framing/zoom, 3:4, no letterbox |
| `medium` | 4 | soft | art-style matches target (pencil, anime, oil, photoreal…) |
| `palette` | 4 | soft | color treatment matches target |
| `line_quality` | 4 | soft | mark/edge character matches target |

Hard gates <5 or soft gates <4 on any anchor → that anchor fails. All anchors pass → Seedance.

## Per-session & per-run caps

- **Reviser per session**: 8 LLM turns, 3 `generate_keyframe` calls per anchor, 6 total KF calls, 4-minute wall clock. On any cap, driver force-closes and auto-builds a `submit_revision`-shaped delta from the newest KF per touched anchor, tagged `driver_forced:true`.
- **Outer loop**: max 3 evaluate/revise cycles, then halt (exit 2).
- **Infra errors**: one retry after 5s for 5xx/429/timeout/malformed-JSON; 4xx (except 429) and ffmpeg errors fail fast (exit 3). Retries do not consume QC attempts.

## Input handling

`--raw-video` (required), `--style-image` (optional), `--style-prompt` (optional, used if no image) each accept local path / `https://` / `asset://`. `asset://` resolution uses AK/SK HMAC v4. Downloads capped at 500 MB into a single `tempfile.TemporaryDirectory` cleaned up on exit. `ffprobe` gets video duration for default anchor timestamps. `ARK_API_KEY` from env (or `--ark-api-key`); `ARK_ACCESS_KEY`/`ARK_SECRET_KEY` only required for `asset://` inputs.

## Round-0 bootstrap

Driver extracts 3 raw frames via ffmpeg, then fires 3 Seedream i2i calls in parallel (`ThreadPoolExecutor`) using a conservative geometry-preserving seed prompt + a negative prompt banning morphing/letterbox/crops/etc. Seeds are stored per anchor and reused across revision rounds unless the Reviser passes `force_new_seed:true`.

## Prompt layout (visual order matters for vision)

**Evaluator**: style ref (image or prompt text) → labeled raw frames (geometry ground truth) → labeled styled KFs per anchor (`[NEW — score]` or `[previously approved — coherence only]`) → rubric/thresholds/schema/history → instruction "Return exactly one JSON object."

**Reviser**: style ref → labeled raw frames → labeled approved KFs (`[approved, do not modify]`) → labeled failing KFs (`[needs fix]`) → per-anchor failure rationales + `suggested_focus` → prior-rounds summary (driver-built compact JSON, ~100–300 tokens) → tool instructions + caps.

Round 1 scores all anchors; rounds ≥2 only score NEW anchors (rescope rule), but coherence notes are always written.

## Outputs

- **stdout**: one compact JSON line (pipeline-friendly: `status`, `final_keyframes` on pass).
- **`--output-kfs-json`** (exit 0 only):
  ```json
  {
    "status": "passed",
    "keyframes": [
      {"anchor_id":"A1","timestamp_sec":2.0,"image_url":"https://…",
       "prompt_used":"…","negative_prompt_used":"…","seed":456}
    ],
    "aspect_ratio":"3:4","qc_rounds_taken":2,"qc_report_path":"qc_report.json"
  }
  ```
  Caller reads `keyframes[*].image_url` and passes them to Seedance as reference images.
- **`--report-path`** (always): pretty JSON with per-round scores, tool-call audit trail, driver-forced flags, duration, total KF generations, version IDs, failure reason.
- **Exit codes**: 0 = passed (Seedance-ready), 2 = QC failed after 3 attempts, 3 = infrastructure error.

## Dependencies

- Python 3.10+, `requests`, Pillow.
- System `ffmpeg` and `ffprobe` on PATH (overridable via `--ffmpeg-path`/`--ffprobe-path`).
- No SDKs, no numpy/opencv/torch, no built-in agent tools enabled.

## Files

Single file: `qc_gate.py`. Prompts (`EVALUATOR_SYSTEM_PROMPT`, `REVISER_SYSTEM_PROMPT`, `ROUND0_SEED_PROMPT`, `ROUND0_NEGATIVE_PROMPT`) and constants (caps, URLs, model IDs, aspect) are UPPER_SNAKE_CASE module-level constants at the top for easy tuning. All tunables live in-code for v1; no flags for them.

## Implementation order

1. Argparse + constants + input resolver (local/https/asset://) + temp lifecycle + ffprobe/ffmpeg wrappers.
2. REST clients (inference + Assets OpenAPI HMAC signing) + Seedream i2i (parallel round-0, sanity checks).
3. Agent bootstrap (environment + Evaluator/Reviser agents with 3 custom tools and optional skill attachment) + cache-file logic with prompt-hash PATCH.
4. Session primitives: create session, send `user.message` with ordered image blocks, 2s cursor polling, post `user.custom_tool_result`.
5. Evaluator round: build prompt blocks, parse JSON with retry, apply thresholds to compute failures.
6. Reviser round: build prompt blocks, tool dispatch, per-session caps (turns/KF-per-anchor/total-KF/wall-clock), driver-forced submit.
7. Outer loop (3-cycle max), inter-round history builder, best-so-far tracking.
8. Report writers + exit codes.
9. Infra error handling (retry-once, fast-fail paths, stage tagging).
10. Smoke test against `unity-5.mp4` + `handDrawnStyle.jpeg`; tune i2i strength constant and rubric prompts until the Q2 frame-2 regression (stained-glass buildings, grey roof rectangle) is caught round 1 and fixed round 1 or 2.
11. Drop `qc_gate.py` into `unity_handdrawn_pipeline_vodfree.md` before the existing Seedance call and republish the Feishu doc.
