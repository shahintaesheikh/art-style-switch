---
name: byteplus-modelark-assets
description: >
  Work with BytePlus ModelArk's Private Asset Library (Assets API). Use this skill whenever the user mentions "ModelArk assets", "asset library", "asset://", "CreateAsset", "CreateAssetGroup", private assets, uploading reference images/videos/audio to ModelArk, or wants to reference a media file by asset ID in a Seedance/Seedream generation call. Also trigger when the user is combining asset URIs with video/image generation (Seedance 2.0 reference_image/reference_video/reference_audio roles), asks how to poll asset upload status, asks about asset quotas or Advanced Creation Rights, or hits Asset API errors (SubscriptionRequired, asset not found, ProjectName mismatch, Status: Failed). Do NOT use this skill for VOD (media processing/transcode) or TOS (raw object storage) operations — those are separate products.
---

# BytePlus ModelArk Private Asset Library

ModelArk's private asset library lets you upload images, videos, and audio once, then reference them in Seedance / Seedream generation calls by short `asset://<id>` URIs instead of juggling public URLs or base64 blobs. This skill covers the management-plane APIs (create/poll/list/delete asset groups and assets) and how to use the resulting URIs in inference calls.

## Two planes — don't mix them up

This is the #1 source of bugs. ModelArk has **two separate APIs** with different hosts, auth, and naming conventions:

| Plane | Host | Auth | Body style |
|---|---|---|---|
| **Inference** (chat, images, video gen) | `https://ark.ap-southeast.bytepluses.com/api/v3` | Bearer `ARK_API_KEY` | snake_case (`image_url`, `video_url`) |
| **Assets OpenAPI** (Create/Get/List/Delete) | `https://ark.ap-southeast-1.byteplusapi.com` | **AK/SK HMAC-SHA256 v4** | PascalCase (`GroupId`, `AssetType`, `URL`) |

- Assets APIs use query string `?Action=<Action>&Version=2024-01-01` with `ServiceName: "ark"`, region `ap-southeast-1`.
- Inference APIs use OpenAI-style paths (`/api/v3/contents/generations/tasks`).
- Never POST Bearer to the OpenAPI host, and never add `?Action=` to the inference host.

## Credentials needed

- `ARK_API_KEY` — inference key from https://console.byteplus.com/ark/region:ark+ap-southeast-1/apikey (Bearer)
- `BYTEPLUS_ACCESS_KEY` / `BYTEPLUS_SECRET_KEY` — AK/SK pair from the BytePlus IAM console, used to sign Assets OpenAPI requests
- (Optional) `ARK_PROJECT_NAME` — defaults to `"default"`. Must match between asset upload and inference endpoint.

Enable models in the ModelArk console before calling them:
- Video: `dreamina-seedance-2-0-260128`
- Image (i2i): `doubao-seedream-5-0-lite-260128` (⚠ `dola-seedream-5-0-pro-260628` does NOT accept the `image` param)

## Supported asset types and file constraints

`CreateAsset` accepts three `AssetType` values:

| AssetType | Formats | Size | Other constraints |
|---|---|---|---|
| `Image` | jpeg, jpg, png, webp, bmp, tiff, gif, heic, heif | <30 MB | W and H each in (300, 6000) px; W/H ratio in (0.4, 2.5) — i.e. between 1:2.5 portrait and 2.5:1 landscape, exclusive |
| `Video` | mp4, mov | ≤50 MB | 2–15 s duration; 24–60 fps; W and H each in [300, 6000]; W*H between 409,600 and 2,086,876 (≈ up to ~1444×1444; covers 480p/720p/1080p standard buckets); W/H in [0.4, 2.5] |
| `Audio` | wav, mp3 | ≤15 MB | 2–15 s duration |

**Upload is URL-only.** The CreateAsset body takes a `"URL"` field that must be a publicly reachable HTTPS URL the asset service can GET. There is no multipart form, no file bytes, no base64. Stage the file first (TOS pre-signed URL, S3 pre-signed, any CDN, or even a `cloudflared` tunnel to `python -m http.server` for local testing).

## Asset API catalog

All calls: `POST https://ark.ap-southeast-1.byteplusapi.com/?Action=<ACTION>&Version=2024-01-01`, HMAC-signed, `Content-Type: application/json`.

| Action | Purpose | Key body fields |
|---|---|---|
| `CreateAssetGroup` | Make a group (bucket for related assets) | `Name` (1-64c), optional `Description`, `GroupType` (default `"AIGC"`), optional `ProjectName` |
| `CreateAsset` | Upload an asset (async) | `GroupId` (required), `URL` (required, public HTTPS), `AssetType` (`Image`/`Video`/`Audio`, required), optional `Name`, `ProjectName`, `Moderation.Strategy` (`"Default"` or `"Skip"`) |
| `GetAsset` | Poll one asset | `Id` (required), optional `ProjectName`. Returns `Status` (`Processing`/`Active`/`Failed`) and a 12-hour-signed `URL` when Active |
| `ListAssets` | List assets (paginated) | `Filter.GroupId`, `Filter.Statuses[]`, `Filter.AssetType`, `Filter.Name` (fuzzy), `PageNumber`, `PageSize` (max 100), `SortBy`/`SortOrder` |
| `UpdateAsset` | Rename an asset | `Id`, `Name` (URL/type/group are immutable — delete and re-upload to change) |
| `DeleteAsset` | Delete an asset | `Id` |
| `GetAssetGroup` / `ListAssetGroups` / `UpdateAssetGroup` / `DeleteAssetGroup` | Group management — same shape | |

Every response uses the standard BytePlus envelope:
```json
{
  "ResponseMetadata": {"RequestId":"...", "Action":"...", "Version":"2024-01-01", "Service":"ark", "Region":"ap-southeast-1", "Error":{...}},
  "Result": { ... }
}
```
Always log `RequestId` when troubleshooting — BytePlus support needs it.

Returned IDs: groups look like `group-<YYYYMMDDHHmmss>-<5char>`; assets look like `asset-<YYYYMMDDHHmmss>-<5char>` (example: `asset-20260222234430-mxpgh`).

## Asset status machine

- `Processing` — just created; platform is downloading from your URL, running moderation, extracting features. Only `GetAsset` and `DeleteAsset` allowed.
- `Active` — ready. The `URL` field now contains a signed TOS download URL (12-hour TTL; call `GetAsset` again to refresh). Safe to reference as `asset://<id>` in generation.
- `Failed` — moderation rejected, URL unreachable, or file violated constraints. Only `DeleteAsset` allowed. Re-upload with a fixed source.

**Polling pattern:**
1. Call `GetAsset` immediately after `CreateAsset` returns.
2. If `Processing`, wait 3 s and retry. After the first 30 s back off to 10 s intervals.
3. Images typically go Active in 3–10 s; videos/audio in 10 s–2 min.
4. Time out at ~5 minutes; if still Processing, verify the source URL is reachable and re-check constraints.

## HMAC signing (Python reference)

The official Python SDK (`byteplus-python-sdk-v2[ark]`) covers inference (`Ark` client) but does **not** wrap the Assets OpenAPI. Implement HMAC-SHA256 v4 signing inline. Required headers: `Content-Type`, `Host`, `X-Date` (UTC `YYYYMMDDTHHMMSSZ`), `X-Content-Sha256` (hex SHA-256 of the body).

```python
import os, json, time, hashlib, hmac, datetime, urllib.request

AK = os.environ["BYTEPLUS_ACCESS_KEY"]
SK = os.environ["BYTEPLUS_SECRET_KEY"]
REGION, SERVICE, HOST = "ap-southeast-1", "ark", "ark.ap-southeast-1.byteplusapi.com"
BASE = f"https://{HOST}"

def _hmac(k, m): return hmac.new(k, m.encode("utf-8"), hashlib.sha256).digest()

def ark_openapi(action: str, body: dict) -> dict:
    body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    now = datetime.datetime.utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    scope = f"{date_stamp}/{REGION}/{SERVICE}/request"
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical_headers = (f"content-type:application/json\nhost:{HOST}\n"
                          f"x-content-sha256:{body_hash}\nx-date:{x_date}\n")
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_req = (f"POST\n/\nAction={action}&Version=2024-01-01\n"
                     f"{canonical_headers}\n{signed_headers}\n{body_hash}")
    string_to_sign = (f"HMAC-SHA256\n{x_date}\n{scope}\n"
                      f"{hashlib.sha256(canonical_req.encode('utf-8')).hexdigest()}")
    k_date = _hmac(SK.encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, REGION)
    k_service = _hmac(k_region, SERVICE)
    k_signing = _hmac(k_service, "request")
    sig = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = (f"HMAC-SHA256 Credential={AK}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={sig}")
    req = urllib.request.Request(
        f"{BASE}/?Action={action}&Version=2024-01-01",
        data=body_bytes, method="POST",
        headers={"Content-Type": "application/json", "Host": HOST,
                 "X-Date": x_date, "X-Content-Sha256": body_hash,
                 "Authorization": auth},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))
```

For Go, the SDK supports this natively via `byteplus/universal.DoCall` with `ServiceName:"ark"`. Java uses the generic OpenAPI caller in `byteplus-java-sdk-v2-core`. If the user prefers another language, sign per the same canonical-request scheme.

## Using asset URIs in generation calls

Once an asset is `Active`, reference it by URI: `asset://<asset_id>` — same slot that accepts HTTPS URLs. Place it in the `.url` field of a multimodal content item:

```json
{
  "type": "image_url",  // or "video_url" or "audio_url"
  "image_url": { "url": "asset://asset-20260222234430-mxpgh" },
  "role": "reference_image"  // or "reference_video" / "reference_audio"
}
```

| AssetType in library | Content `type` field | `role` values |
|---|---|---|
| `Image` | `image_url` → `image_url.url` | `reference_image`, plus first-frame/last-frame roles if applicable |
| `Video` | `video_url` → `video_url.url` | `reference_video` |
| `Audio` | `audio_url` → `audio_url.url` | `reference_audio` (cannot be used alone — at least one image or video reference must accompany it) |

**Prompt referencing:** in the text prompt refer to assets by **type + 1-based ordinal among that type**, e.g. `Image 1`, `Image 2`, `Video 1`, `Audio 1`. Do NOT paste raw asset IDs into the prompt text. The ordinal is the item's position among same-type content entries in the request body, regardless of interleaving.

**ProjectName lock:** the inference endpoint used for the generation call must live in the same project as the asset (default both to `"default"` if you're not using IAM projects). Mismatched ProjectName is a documented cause of "upload succeeded but asset isn't found by inference" errors.

## Quotas and tiers

Asset quotas come from Advanced Creation Rights[[Advanced Creation Rights]](https://docs.byteplus.com/en/docs/modelark/2377608):

| Tier | Price | Virtual/Generic API upload | Asset / Group quota | CreateAsset QPM |
|---|---|---|---|---|
| Basic Creation Rights (free) | $0 | ❌ console-only for real humans; no API virtual/generic upload | 50 / 50 | 3 |
| Advanced Creation Rights (Entry) | Free (enterprise verification required) | ✅ | 50 / 50 | 3 |
| Advanced Creation Rights (Pro) | $14k/yr or $1.4k/mo | ✅ | 1,000,000 / 1,000,000 | 120 |
| Advanced Creation Rights (Premium) | $42k/yr or $4.2k/mo | ✅ | 5,000,000 / 5,000,000 | 300 |

- `GetAsset`: 100 QPS (tier-independent per docs)
- `GetAssetGroup`: 10 QPS
- After a paid subscription expires, there is a 15-day grace period during which existing assets still work but new API uploads are blocked; after 15 days, paid-tier assets are **irrecoverably deleted**.

If a call returns `SubscriptionRequired`, the account needs at least Advanced Creation Rights Entry (which is free once enterprise verification is complete).

## Typical end-to-end flow

1. One-time: `CreateAssetGroup` → save `GroupId` (reuse across runs).
2. For each new media file to register: stage it at a reachable HTTPS URL → `CreateAsset{GroupId, URL, AssetType, Name}` → save `Id`.
3. Poll `GetAsset(Id)` every 3–10 s until `Status == "Active"`.
4. Build the generation request using `asset://<id>` in `image_url.url` / `video_url.url` / `audio_url.url` with the appropriate `role`.
5. Poll the generation task (inference plane, Bearer auth) until `succeeded`; download from `content.video_url` (URL valid ~48 h).
6. For bulk cleanup, `ListAssets` by group and `DeleteAsset` each, then `DeleteAssetGroup`.

## Common errors and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| 404 / `InvalidActionOrVersion` on `https://ark.ap-southeast-1.byteplusapi.com` | Wrong host, wrong Version, or query string not on the URL | POST to `https://ark.ap-southeast-1.byteplusapi.com/?Action=X&Version=2024-01-01` exactly; body is JSON, not form |
| Auth failures on Assets calls | Used Bearer instead of AK/SK, or wrong region/service in signer | Use HMAC-SHA256 v4 per the reference above; region `ap-southeast-1`, service `ark` |
| Auth failures on inference calls | Used AK/SK on the inference host | Use `Authorization: Bearer $ARK_API_KEY` against `ark.ap-southeast.bytepluses.com/api/v3` |
| `SubscriptionRequired` | Account on Basic free tier trying API upload of virtual/generic assets | Complete enterprise verification for Entry tier, or upload via console |
| Asset stays `Processing` for minutes then `Failed` | Source URL unreachable from ModelArk, or file violates constraints (wrong container, W*H out of range, duration >15s) | `curl -I` the URL from a non-corporate network; re-encode with ffmpeg (`-c:v libx264 -pix_fmt yuv420p`); verify constraints above |
| Generation says "asset not found" | ProjectName mismatch, or asset not yet `Active`, or typo in URI format | Wait for Active; use exactly `asset://asset-...` (no extra slashes, no host); confirm `ProjectName` matches between upload and inference endpoint |
| Asset uploads work but console doesn't show it | Using a different ProjectName than the console view | Pass the same `ProjectName` used in the console, or omit it to use `default` |
| `GetAsset` returns an empty `URL` field | Asset still `Processing` | Keep polling until `Active`; URL is populated only then and is valid 12 h |
| Video asset rejected with dimension error | W×H outside [409,600; 2,086,876] | Re-encode to a standard bucket (480p/720p/1080p at a supported ratio — see Seedance resolution table) |

## When not to use this skill

- For **media processing** (transcode, concat, audio extract, watermarking, HLS packaging), use BytePlus VOD or ffmpeg. Asset library stores files; it doesn't transcode.
- For **raw object storage** at scale (lifecycles, CDN origin, public buckets), use BytePlus TOS or S3.
- For **real-human portrait** assets with consent flows (face verification, QR-code authorization), use the dedicated `CreateVisualValidateSession` flow — that is a separate path outside this skill's scope.
