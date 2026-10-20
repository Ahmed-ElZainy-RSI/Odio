# Contract: Backend ⇄ AI Engine (V1)

Authoritative prose: `service/docs/02-api-reference.md`; machine schema: `GET /openapi.json`.

## Inbound

`POST /api/jobs` — `Authorization: Bearer <Keycloak client_credentials token>`

| Field | Job A `PAGES_IMG_AND_TEXT_EXTRACTION` | Job B `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION` |
|---|---|---|
| `jobId` (GUID, idempotency key) | ✔ | ✔ |
| `fileId` | the PDF | the page text file |
| `folderId`, `pathPrefix` | ✔ | ✔ |
| `sourceDownloadUrl` (https, allowlisted host) | ✔ | ✔ |
| `pageNumber` | rejected | required, ≥ 1 |
| `qualityTier` (proposal) | `fast`\|`verified` | `fast`\|`verified` |

Responses: `202` (new or duplicate), `401`, `403`, `409` (same id, different payload), `415`, `422`, `503`.

## Outbound (engine → Backend)

| Step | Call |
|---|---|
| token | `POST {TOKEN_URL}` `grant_type=client_credentials` (cached, 30 s margin) |
| refresh source | `GET /api/files/{fileId}/get-download-url` |
| metadata (Job A, gates page work) | `PATCH /api/jobs/{jobId}/metadata {pagesCount}` |
| upload 1/3 | `POST /api/files/initialize-upload` → `{fileId, upload:{url, requiredHeaders}}` |
| upload 2/3 | `PUT <presigned url>` |
| upload 3/3 | `POST /api/files/{fileId}/confirm-upload` |
| page done | `PATCH /api/jobs/{jobId}/{page}/completed {artifacts:[...]}` |
| page failed | `PATCH /api/jobs/{jobId}/{page}/failed {reason}` |
| job failed | `PATCH /api/jobs/{jobId}/failed {reason}` |

Artifacts: Job A → `Image` + `Text` per page. Job B → `Audio` + `JSON` (word timings keyed by token id, with `anchor_ratio`).
