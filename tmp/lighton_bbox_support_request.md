Subject: `include_bboxes` on POST /api/v3/search always returns an empty array

Account email: ahmed.mostafa22200028@gmail.com
API key prefix: fUNETLRL

## What I'm trying to do

Get bounding boxes for search-matched chunks via `POST /api/v3/search` with
`include_bboxes: true`, as documented at
https://docs.lighton.ai/api-reference-v3 (search-document-chunks).

## What happens instead

The request succeeds (HTTP 200), the response includes a `bboxes` field, but
it is always `[]` — never populated — across every variation I tried:

| Condition | Result |
|---|---|
| Scanned PDF, no embedded text layer, 1 page | `bboxes: []` |
| PDF with a full native text layer, 16 pages | `bboxes: []` |
| Tight single-chunk exact-phrase query, `top_k=1, top_n=1, skip_rerank=true` | `bboxes: []` |
| `POST /api/v3/retrieve` (same query) | `chunk.metadata.coords: null` |

Both documents were freshly uploaded and reached `status: "embedded"` /
`parser: "v2.2.1"` before I queried them, so the "not available for
pre-v2.2.1 chunks" constraint in the docs doesn't apply.

## Reproduction

```bash
# 1. upload (workspace_id from a workspace where the key has owner/write role)
curl -X POST https://api.lighton.ai/api/v3/files \
  -H "Authorization: Bearer $LIGHTON_API_KEY" \
  -F "workspace_id=<workspace_id>" \
  -F "file=@page.pdf"

# 2. poll GET /api/v3/files/<id> until status == "embedded"

# 3. search
curl -X POST https://api.lighton.ai/api/v3/search \
  -H "Authorization: Bearer $LIGHTON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "query": "<any phrase known to be on the page>",
        "mode": "text",
        "include_bboxes": true,
        "file_id": [<file_id>],
        "top_k": 1,
        "top_n": 1,
        "skip_rerank": true
      }'
```

Response `results[0].bboxes` is `[]` in every case.

## Question

Is `include_bboxes` gated behind a plan/tier not visible as an explicit
error, or is there an additional parameter/precondition not covered in the
public docs that's needed to actually populate it? We're evaluating LightOn
as our OCR engine specifically because page-level bounding boxes are a hard
requirement for our product (a synced page-image + text reader), so this is
blocking that decision.
