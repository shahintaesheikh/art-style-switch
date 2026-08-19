---
name: serpapi-google-images
description: Reference for calling SerpApi's Google Images API (engine=google_images) — request parameters and JSON response structure. Use whenever writing or debugging code that queries SerpApi for Google Images results, e.g. building an image search tool, scraper, or agent action that needs image results, thumbnails, or shopping/related-search data from Google Images.
---

# SerpApi Google Images API

Endpoint: `GET https://serpapi.com/search?engine=google_images`

## Request

Required params:
- `q` — search query (supports `site:`, `intitle:`, `inurl:`, `filetype:`, etc.)
- `engine` — always `google_images`
- `api_key` — your SerpApi key

Common optional params:
- `location` — city-level string (mutually exclusive with `uule`)
- `google_domain`, `gl` (country code), `hl` (lang code) — localization
- `ijn` — page number (0-indexed, pagination)
- `imgar` — aspect ratio: `s` square, `t` tall, `w` wide, `xw` panoramic
- `imgsz` — size filter: `l`, `m`, `i`, or thresholds like `4mp`, `12mp`
- `image_color` — e.g. `bw`, `red`, `blue`, `black`, `transparent`
- `image_type` — `face`, `photo`, `clipart`, `lineart`, `animated`
- `licenses` — `f`, `fc`, `fm`, `fmc`, `cl`, `ol` (usage rights)
- `safe` — `active` or `off`
- `chips` — Google-suggested filter string (from `suggested_searches` in a prior response)
- `period_unit`/`period_value` or `start_date`/`end_date` (`YYYYMMDD`) — time filtering (mutually exclusive)
- `device` — `desktop` (default), `tablet`, `mobile`
- `no_cache` — `true` to bypass 1h cache
- `output` — `json` (default), `html`, or `md`

Full param list: https://serpapi.com/google-images-api

## Response shape

```jsonc
{
  "search_metadata": { "id", "status", "json_endpoint", "created_at", "total_time_taken", ... },
  "search_parameters": { /* echoes the request params actually used */ },
  "search_information": { "image_results_state": "Results for exact spelling" },

  "images_results": [
    {
      "position": 1,
      "thumbnail": "https://...",       // hosted thumbnail
      "original": "https://...",        // full-res source image URL
      "original_width": 3200,
      "original_height": 2000,
      "title": "...",
      "source": "Wikipedia",            // site name
      "link": "https://...",            // page hosting the image
      "is_product": false,              // true if shopping-linked
      "in_stock": true,                 // only present if is_product
      "tag": "Recipe" | "Licensable",   // optional
      "license_details_url": "...",     // only with license filters
      "related_content_id": "...",      // pass to google_images_related_content engine
      "serpapi_related_content_link": "..."
    }
  ],

  "shopping_results": [  // present when query has shopping intent
    { "position", "title", "price", "extracted_price", "link", "source", "thumbnail", "extensions" }
  ],

  "suggested_searches": [  // Google's filter chips, only on ijn=0
    { "name", "chips", "link", "serpapi_link", "thumbnail" }
  ],

  "related_searches": [
    { "query", "highlighted_words": [], "link", "serpapi_link", "thumbnail" }
  ],

  "serpapi_pagination": { "current": 0, "next": "https://serpapi.com/search.json?...ijn=1..." }
}
```

## Notes for building agents/tools around this API

- Check `search_metadata.status` (`Success` / `Error`) before reading results; on `Error`, read the `error` field.
- Paginate by following `serpapi_pagination.next` rather than hand-building `ijn`.
- Images from PDFs may return `original` as `x-raw-image:///<hash>` instead of a real URL — these can't be fetched directly.
- `is_product`/`in_stock`/`shopping_results` only appear for queries with commercial intent (e.g. product names).
- `chips` values come from a prior `suggested_searches` response — use them to drill into a filtered search rather than guessing the string format.