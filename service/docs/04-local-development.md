# Local development

## Setup

```bash
cd service
python -m pip install -e '.[dev]'
```

`.[ai]` additionally installs the AI dependencies (`pymupdf`, `pillow`, `faster-whisper`,
`google-genai`). The text-layer OCR adapter needs `pymupdf` and `pillow`; if they are
absent, the adapter fails cleanly with `MODEL_UNAVAILABLE` rather than at import.

Python 3.11+ is required. The repo's existing suite is verified against 3.11.15.

## Run the tests

```bash
python -m pytest -q                    # 616 passed, ~40s
python -m pytest -q -m "not slow"      # 607 passed, ~19s — the fast loop
python -m pytest tests/unit -q         # 429 tests, ~6s
python -m pytest tests/e2e -q          # 103 tests — protocol, faults, durability, quotas
```

`-m "not slow"` skips the nine tests that render a real 16-page book at 2×, which are the
only genuinely slow part.

## Lint and format

```bash
python -m ruff check .
python -m ruff format --check .
```

Both are clean and should stay that way. One rule is disabled for tests: `SIM300`
misreads `assert MODULE_CONSTANT == expected_literal` as a Yoda condition, and
`actual == expected` is the correct order for a pytest assertion.

## Run the whole thing locally

One command brings up three real processes — the fake Backend, the API, and a worker —
talking over real sockets. No network, no credentials, no paid API calls.

```bash
cd service
./devtools/local_stack.sh
```

It prints the endpoints and the exact `curl` to submit a book. What is running:

| | | |
|---|---|---|
| API | `http://127.0.0.1:9099` | the real service: `/api/jobs`, `/health`, `/ready`, `/metrics`, `/ops/budget`, `/docs` |
| worker | — | a real `ai-engine-worker` process, claiming from the same SQLite file |
| fake Backend | `http://127.0.0.1:9100` | [`devtools/fake_backend_server.py`](../devtools/fake_backend_server.py) — Keycloak's token endpoint, the Backend's five book endpoints, and presigned blob storage |

The fake is the **same** `tests/fakes/backend.py` the test suite runs against, served over a
socket rather than an ASGI transport — deliberately not a second implementation, which
would drift from the one the tests trust.

```bash
./devtools/local_stack.sh logs worker    # tail a process
./devtools/local_stack.sh stop
```

### Submit a book and watch it happen

```bash
JOB=$(uuidgen | tr 'A-Z' 'a-z')
curl -s -X POST http://127.0.0.1:9099/api/jobs -H 'content-type: application/json' -d '{
  "jobId": "'$JOB'",
  "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "fileId": "book-1",
  "folderId": "books/reading-first/grade-1",
  "pathPrefix": "s3-prefix/2026/09",
  "sourceDownloadUrl": "http://fake.localhost:9100/_source/book-1"
}'
# -> 202 {"jobId":"...","status":"QUEUED","duplicate":false,...}

curl -s http://127.0.0.1:9099/api/jobs/$JOB    # our view
curl -s http://127.0.0.1:9100/_state           # the Backend's view
```

A real 16-page Arabic book (`assets/PDF Samples/النجوم.pdf`) finishes in about three
seconds:

```
state             : COMPLETED
pages             : {"reportedCompleted": 16, "reportedFailed": 0, "nonTerminal": []}
reports           : 16 pages, all "completed"
uploads           : 32 files, 32 confirmed   (one image + one text per page)
tokens issued     : 1                        (one cached token for ~117 calls)
call order        : source_get -> token -> metadata -> initialize_upload
                    -> put_upload -> confirm_upload -> page_completed
```

Two things in that output are the V1 contract, not incidental. `metadata` lands before the
first `initialize_upload` because the page count is a real **gate**, not a notification.
And one token covers the whole book.

Pull the artifacts back out to see they are genuine:

```bash
curl -s http://127.0.0.1:9100/_state | python -m json.tool     # fileIds and content types
curl -s http://127.0.0.1:9100/_artifact/<fileId>               # the exact bytes we uploaded
```

The `text/plain` artifact for page 3 is real diacritized Arabic read from the PDF's
embedded text layer, which is why this run costs nothing:

```
الأَبُ فِي هَذِهِ الأَوِنَةِ كَانَ الْبَحَّارَةُ يَعْتَمِدُونَ عَلَى النُّجُومِ لِإِرْشَادِهِمْ
```

### Why `fake.localhost` and not `127.0.0.1`

The API **refuses** a `sourceDownloadUrl` whose host is literally `localhost`,
`127.0.0.1`, `::1` or `0.0.0.0` — an SSRF control that should not be switched off to make
a demo work. `fake.localhost` resolves to loopback (RFC 6761) without being on that
blocklist, so the control stays on and the loop still runs. Verified:

```
http://127.0.0.1:9100/...       -> 422  sourceDownloadUrl host is not permitted
http://localhost:9100/...       -> 422  sourceDownloadUrl host is not permitted
http://fake.localhost:9100/...  -> 202  QUEUED
```

The first two being refused is the system working, not something to configure around.

### Break it on purpose

The fake injects faults at runtime, so you can watch the retry and recovery paths without
writing a test:

```bash
curl -s -X POST http://127.0.0.1:9100/_faults -H 'content-type: application/json' \
  -d '{"put_failures": 3, "confirm_failures": 2, "callback_failures": 2, "source_expired_times": 1}'
```

Submit the same book again and it still completes — 16/16 pages, no page reported twice,
with the surplus calls visible in `callCount` (121 against 114 for a clean run). The field
names are `FaultConfig`'s in [`tests/fakes/backend.py`](../tests/fakes/backend.py); the
convention is worth knowing:

| Form | Meaning |
|---|---|
| `put_failures: 3` | fail the next 3 attempts, then succeed — **transient** |
| `put_status: 500` | fail *every* attempt with 500 — **permanent** |
| `source_expired_times: 1` | one presigned-URL `403`, to exercise the refresh path |
| `retry_after: "1"` | attach `Retry-After` to the failures, to exercise honouring it |

Setting both `put_status` and `put_failures` makes the fault permanent, which is a useful
thing to see too: pages report `failed` rather than hanging, and the job still reaches a
terminal state for every page. That is INVARIANT-1 under a real outage.

`POST /_reset` clears the fake's memory between runs.

### Job B (audio and timings)

Job B needs a text file as its source and a TTS engine. `TTS_ENGINE=synthetic` (the stack
script's default) emits genuine WAV audio with no API key, but **alignment downloads the
faster-whisper weights on first use** — 464 MB — so the first Job B is slow and needs
network access once. If you would rather not, the 21 Job B tests cover the same path with
an injected transcriber:

```bash
python -m pytest tests/e2e/test_job_b_end_to_end.py -q
```

## Serve just the API by hand

```bash
AI_ENGINE_JSON_LOGS=false \
SOURCE_URL_ALLOWED_HOSTS=storage.example.com \
python -m uvicorn --factory ai_engine.api.app:create_app --port 9099
```

`AI_ENGINE_JSON_LOGS=false` gives readable console output; production wants the default
JSON. Then:

```bash
curl -s localhost:9099/health
curl -s localhost:9099/ready
curl -s localhost:9099/openapi.json | python -m json.tool | head -40

JOB=$(uuidgen | tr 'A-Z' 'a-z')
curl -s -X POST localhost:9099/api/jobs \
  -H 'content-type: application/json' \
  -d '{
    "jobId": "'"$JOB"'",
    "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
    "fileId": "book-1",
    "folderId": "books/g1",
    "pathPrefix": "s3/2026/09",
    "sourceDownloadUrl": "https://storage.example.com/book.pdf?X-Amz-Signature=abc"
  }'
# -> 202 {"jobId":"...","status":"QUEUED","duplicate":false,...}

curl -s -X POST localhost:9099/api/jobs -H 'content-type: application/json' -d '{...same...}'
# -> 202 {"duplicate":true}   idempotent

curl -s localhost:9099/api/jobs/$JOB
curl -s localhost:9099/metrics | grep jobs_accepted
```

Started this way the API alone will not process the job — the worker is a separate
entrypoint (`./devtools/local_stack.sh` starts both). They share state through the store: `JOB_STORE=sqlite` is the default, so start a worker against the *same*
`JOB_STORE_PATH` and it will claim what the API accepted:

```bash
JOB_STORE_PATH=/tmp/ai-engine/jobs.db \
LEGACY_REPO_ROOT=$(cd .. && pwd) \
AI_ENGINE_CLIENT_SECRET=dev-secret \
AI_ENGINE_JSON_LOGS=false \
python -c 'from ai_engine.main import run_worker; run_worker()'
```

Point the API at the same path and the handoff is real. (With `JOB_STORE=memory` it cannot
work at all: the store lives inside one process.) To watch a job run end to end without a
Backend, use the script below.

## Or just run the container

```bash
cd ..    # the build context is the repository root: the service wraps src/pipeline
docker build -f service/Dockerfile -t ai-engine:dev .
docker compose -f service/docker-compose.yml up      # or `docker-compose` if that is your binary
```

One image, three roles — `ai-engine-api`, `ai-engine-worker`, `ai-engine-reconciler` — all
sharing one `/var/lib/ai-engine` volume. See
[08-operations.md](08-operations.md#running-it).

## Poke at the adapters directly

```bash
PYTHONPATH=. python -c "
from ai_engine.adapters.ai.renderer_pymupdf import PyMuPdfRenderer
from ai_engine.adapters.ai.ocr_textlayer import TextLayerOcr
from pathlib import Path
REPO = Path('/Users/you/path/to/ocr_review_models')
pdf = str(REPO / 'assets/PDF Samples/النجوم.pdf')
r = PyMuPdfRenderer()
print('pages:', r.page_count(pdf), 'text layer:', r.has_text_layer(pdf))
tokens, prov = TextLayerOcr(legacy_repo_root=REPO).extract_page(
    page_image=None, pdf_path=pdf, page_number=3)
print('tokens:', len(tokens)); print(tokens[0])
"
```

## Common problems

| Symptom | Cause |
|---|---|
| `refusing to start: .../.env exists` | Working as designed — see [03-configuration.md](03-configuration.md#why-a-env-on-disk-is-fatal). Remove the file and inject env instead. |
| `error parsing value for field "source_url_allowed_hosts"` | An old build. List settings now accept CSV; update and re-run. |
| `MODEL_UNAVAILABLE: LEGACY_REPO_ROOT is not configured` | The OCR adapter needs the path to the repo root containing `src/`. |
| `SOURCE_INVALID: not a PDF` | The fetched bytes do not start with `%PDF-`. Check what the URL actually serves. |
| `422 sourceDownloadUrl host is not on the allowlist` | Add the host to `SOURCE_URL_ALLOWED_HOSTS`. |
| e2e tests skipped | `assets/PDF Samples/النجوم.pdf` is missing; the suite skips rather than failing. |
| `async def functions are not natively supported` | `pytest-asyncio` is not installed — `pip install -e '.[dev]'`. |
| Jobs accepted but nothing runs | The API and the worker are on different stores. They must share `JOB_STORE_PATH`, and `JOB_STORE=memory` cannot be shared at all. |
| `MODEL_UNAVAILABLE: GEMINI_API_KEY is not configured but OCR_ENGINE resolved to 'gemini'` | The document has no text layer and `auto` fell through to the paid path. Set the key, or use a PDF with a text layer. The job fails once, before any page is rendered. |
| A book stops halfway with no failures | A provider budget is exhausted and the rest is parked. `curl localhost:9099/ops/budget`. |
| `schema version mismatch: database has 2, this build expects 3` | A `jobs.db` from an older build. Delete it (dev) or migrate it. |
