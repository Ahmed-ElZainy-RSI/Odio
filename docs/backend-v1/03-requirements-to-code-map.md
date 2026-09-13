# Requirements → Code Mapping

One row per requirement from [02-v1-requirements.md](02-v1-requirements.md). Status vocabulary:
`EXISTS` | `PARTIAL` | `MISSING` | `NEEDS REFACTOR` | `UNCLEAR`.

Where a row says `MISSING`, it means an exhaustive search found nothing. The searches run:

```bash
# no web framework, queue, DB, storage or auth client anywhere
grep -rniE '\b(fastapi|flask|uvicorn|gunicorn|django|starlette|celery|rq\.|redis|rabbitmq|pika|kombu|
  sqlalchemy|psycopg|sqlite3|pymongo|boto3|minio|keycloak|oauth|jwt)\b' --include='*.py' .
# --> only outbound vendor "Bearer <api key>" header lines. Zero framework hits.

# no deployment or packaging artifacts, and none ever deleted
find . -maxdepth 3 \( -iname 'Dockerfile*' -o -iname 'docker-compose*' -o -iname 'Makefile' \
  -o -iname 'pyproject.toml' -o -iname 'setup.py' -o -iname '*.yml' -o -iname '*.yaml' \)
# --> empty.  git log --all --diff-filter=D confirms none were ever removed.

# no timing/alignment artifact concepts in walidapro
grep -rniE 'timing|timestamp|word_time|highlight|srt|vtt' walidapro --include='*.py'
# --> zero hits.

# no atomic writes or locking in the maintained pipeline
grep -rnE 'os\.replace|\.rename\(|NamedTemporary|fsync|flock' src/pipeline tools --include='*.py'
# --> zero matches.
```

## REQ-AUTH — Authentication

Nothing in the repository performs any form of authentication *inbound*, and the only outbound auth is a
static vendor API key placed in a `requests.Session` header at construction time.

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-AUTH-01 Keycloak `client_credentials` | none | — | `MISSING` | No OAuth2 client of any kind. No `client_id`/`client_secret` concept. | Build a `KeycloakTokenProvider`. `T-5.1` |
| REQ-AUTH-02 in-memory token cache, 30 s margin | none | — | `MISSING` | No token cache, no expiry arithmetic. | `T-5.1` |
| REQ-AUTH-03 do not persist tokens | vacuously true | — | `EXISTS` | Nothing is persisted because nothing exists. | Preserve the property; assert it in tests. `T-6.3` |
| REQ-AUTH-04 handle 401 as expired | `except Exception` retries every status identically | `walidapro/ocr_verifier/lighton_ocr.py:180`, `:197` | `MISSING` | Auth failures are retried like 429s with no refresh. | Status-aware retry classification. `T-1.4`, `T-5.1` |
| REQ-AUTH-05 Bearer header on Backend calls | static vendor keys only | `walidapro/ocr_verifier/lighton_ocr.py:19-21`, `reviewer.py:40-43`, `visual_reviewer.py:176-186`, `src/pipeline/ocr_lighton.py:67` | `MISSING` | These are outbound *vendor* credentials, not the Backend token. Different mechanism entirely. | Build a `BackendClient` with token injection. `T-5.2` |
| REQ-AUTH-06 validate inbound token | none | — | `MISSING` | No HTTP surface exists, so nothing to validate on. Mechanism also unspecified by the PDF (`Q-01`). | Build JWT/JWKS validation. `T-2.2` |

## REQ-JOB — Job intake

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-JOB-01 `POST /api/jobs` → 202 | none | — | `MISSING` | Entry points are `argparse` CLIs only: `src/pipeline/main.py:93`, `walidapro/run.py:9`, `walidapro/run_full_pipeline.py:2470`, plus 10 in `tools/`. | Build the FastAPI app. `T-2.1` |
| REQ-JOB-02 Job A body | none | — | `MISSING` | No `jobId`, `fileId`, `folderId`, `pathPrefix` or `sourceDownloadUrl` concept anywhere. | Request DTO + validation. `T-2.3` |
| REQ-JOB-03 Job B body incl. `pageNumber` | page selection exists as a CLI flag | `src/pipeline/pages.py:14 parse_pages`, `walidapro/ocr_verifier/pdf_render.py:14` | `PARTIAL` | A 1-based page spec parser exists and is reusable, but it is a `--pages "1-5,8"` string, not a single `pageNumber` int from a job body. | Reuse the 1-based convention; new DTO field. `T-2.3` |
| REQ-JOB-04 async background execution | fully synchronous everywhere | every driver is a blocking `for page in …` loop | `MISSING` | No threading, asyncio, multiprocessing or `concurrent.futures` anywhere in `src/pipeline`, `tools/` or `walidapro/`. The one async module, `src/pipeline/run_speak_hume.py:77`, wraps itself in `asyncio.run()` and so **raises `RuntimeError` if called from inside a running event loop**. | Worker role + queue. `T-2.5`, `T-3.1` |
| REQ-JOB-05 persist the job | filesystem side effects only | `src/pipeline/main.py:62-63` `work/{stem}` | `MISSING` | Job state is inferred from whether a file exists with a given `status`. Not addressable by id, not transactional, not restart-safe. | Postgres `jobs` table. `T-4.1` |

## REQ-IDEM — Idempotency

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-IDEM-01 duplicate `jobId` → 202, one execution | resumability by page `status` | `src/pipeline/run_ocr_gemini.py:50-53` (`status in ('extracted','diacritised','spoken')` → skip); `src/tests/test_ocr_resumability.py:40` | `PARTIAL` — wrong key | The *idea* of "don't redo finished work" is implemented and tested. But the key is `(work_dir, page_number)` derived from a **PDF filename stem**, not a caller-supplied GUID. Two POSTs with the same `jobId` would both start and race on one directory with no locking. | `jobs.job_id` PRIMARY KEY + `INSERT … ON CONFLICT DO NOTHING`. `T-2.4` |
| REQ-IDEM-02 store lookup by GUID before scheduling | none | — | `MISSING` | No store, no GUID. | `T-2.4` |
| REQ-IDEM-03 safe under repeated delivery | multi-layer content-hash caches | `walidapro/ocr_verifier/pipeline.py:71-108` (PDF sha256 + dpi + model fingerprint), `:164-263` visual cache, `:265-333` review cache; `diacritization/word_diacritics.py:1020-1049`; `tts/pipeline.py:157-181` | `PARTIAL` | The caches genuinely prevent re-paying for identical work and are keyed on content — good. But they are **per-output-directory local files**, so nothing is shared across worker replicas, and `word_diacritics.py:1143→:1405` is a lock-free whole-file read-modify-write with a network call in the middle: two workers sharing a directory lose each other's entries. | Job-level idempotency in Postgres; re-key content caches on content, not directory. `T-2.4`, `T-3.6` |

## REQ-SRC — Source acquisition

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-SRC-01 `GET sourceDownloadUrl` | local file paths only | `src/pipeline/run_extract.py:37`, `run_ocr_gemini.py:36`, `run_ocr_lighton.py:41` all call `read_bytes()`; `walidapro/run.py:20` `SystemExit`s on any suffix ≠ `.pdf` | `MISSING` | Every entry point requires a local `.pdf`. There is no HTTP fetch of a source anywhere. | `SourceFetcher`. `T-5.3` |
| REQ-SRC-02 refresh expired URL | none | — | `MISSING` | No concept of an expiring input. | `T-5.3` |

## REQ-META — Page count

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-META-01 `PATCH /metadata {pagesCount}` before page work | page count is computed and **printed, then discarded** | `src/pipeline/run_ocr_gemini.py:38-40` (`fitz.open` then `len(doc)`), `run_extract.py:39`, `run_ocr_lighton.py:43`; `walidapro/ocr_verifier/pdf_render.py:58` has `len(doc)` inside the function body and never returns it | `PARTIAL` | The number is available in milliseconds but is never returned by any function. `walidapro` is worse: `--pages` is `required=True` (`run_full_pipeline.py:2496-2504`) so the count is an *input*, never an output. | Add `page_count(path) -> int`; call `metadata` first. `T-3.2`, `T-5.4` |
| REQ-META-02 all N pages terminal (INVARIANT-1) | **actively violated** | `src/pipeline/run_ocr_gemini.py:59-62` `except Exception: print; break`; `run_speak_gemini.py:79-84` `…; return`; `run_ocr_lighton.py:58`; `run_speak_hume.py:63-65`. And `walidapro/ocr_verifier/pipeline.py:1846-1865` writes `<!-- PAGE FAILED: {exc} -->` as the page's text while `run.py` still exits 0 | `MISSING` — and worse than missing | One page's failure silently abandons every remaining page. Under V1 that stalls the book in the Backend permanently. | Per-page isolation, page ledger, outbox, reconciler. `T-4.2`, `T-4.4`, `T-4.5` |

## REQ-UPL — Upload cycle

Nothing in the repository uploads anything. Every artifact is written to a local relative path and left
there (`src/pipeline/run_extract.py:74`, `run_ocr_gemini.py:56,77`, `run_speak_gemini.py:45,58`,
`build_reader.py:331`).

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-UPL-01 `initialize-upload` | none | — | `MISSING` | Nothing computes `sizeInBytes` or sets a `contentType`. | `ArtifactUploader`. `T-5.5` |
| REQ-UPL-02 presigned `PUT` | none | — | `MISSING` | — | `T-5.5` |
| REQ-UPL-03 `confirm-upload` | none | — | `MISSING` | No record of which artifacts were uploaded, so a retry after a partial upload cannot avoid double-confirming. | `T-5.5`, `T-4.3` |
| REQ-UPL-04 content-type enum | implicit only | JPEG at `src/pipeline/ocr_gemini.py:61`; WAV at `run_speak_gemini.py:44-47`; MP3 at `run_speak_hume.py:23` | `PARTIAL` | The right bytes are produced, but MP3 on the Hume path is **not in the V1 enum**. | Constrain the audio path to WAV, or raise `Q-09`. `T-3.5` |
| REQ-UPL-05 one cycle per file, ordered | n/a | — | `MISSING` | No uploader to order. | `T-5.5` |

## REQ-ART — Artifacts

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-ART-01 one image per page | **`EXISTS`** | `src/pipeline/ocr_gemini.py:56-62` renders it; `run_ocr_gemini.py:35` makes `pages/`; `:56` writes `pages/pNNN.jpg`. 119 real JPEGs on disk, median 161 KB. | `EXISTS` | One real gap: `src/pipeline/run_ocr_lighton.py` has **no `pages/` mkdir anywhere**, so the LightOn path produces no image. `tools/build_context_review.py:34-37` compensates by borrowing the sibling Gemini folder's JPEG. | Make image rendering unconditional and engine-independent. `T-3.3` |
| REQ-ART-02 one text file per page | **computed then discarded** | `src/pipeline/run_ocr_gemini.py:65` — `text = " ".join(t["txt"] for t in tokens)` is a local variable, never written | `PARTIAL` | The exact bytes V1 wants already exist in memory for one line of code's lifetime. Median 643 B. | Add a `TextArtifactBuilder` projection. `T-3.4` |
| REQ-ART-03 one audio per page | **`EXISTS`**, but two per page on the walidapro path | `src/pipeline/run_speak_gemini.py:30,44-47` → `audio/pNNN.wav`, 24 kHz mono 16-bit. 95 real WAVs, median 1.0 MB. Versus `walidapro/run_full_pipeline.py:1057-1130` which always synthesizes **female then male**. | `EXISTS` / `NEEDS REFACTOR` | The callback carries a single `Audio` artifact. Dual narration doubles cost and has nowhere to go. | Explicit narrator selector, single output. `T-3.5`, `Q-08b` |
| REQ-ART-04 one timing JSON per page | **exists as data, not as a file** | `src/pipeline/speak.py:99 bind` produces `t`/`t_src`; `run_speak_gemini.py:52-57` persists them **inside** `pNNN.json` alongside OCR provenance and the raw `whisper_words` array | `PARTIAL` | No standalone file. `ls work/*/audio` shows only `.wav`/`.mp3`. A ~3.3 KB projection must be carved from the 8.1 KB mixed record. **Pipeline W cannot do this at all** — zero timing hits across `walidapro/**/*.py`. | `TimingArtifactBuilder` projection; force Job B onto Pipeline S. `T-3.7`, [ADR-009](19-adrs.md) |

## REQ-CB — Callbacks

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-CB-01 `/completed` after both files | none | — | `MISSING` | Progress is signalled by rewriting a local JSON file: `walidapro/run_full_pipeline.py:1301` (`page_status.json`), `:2723` (`book_report.json` rewritten after every page). No callback hook, generator or callable parameter exists in any page loop. | `JobCallbacks` + outbox. `T-5.6`, `T-4.4` |
| REQ-CB-02 / REQ-CB-03 `artifactType` enums | none | — | `MISSING` | No artifact type vocabulary. | `T-4.3` |
| REQ-CB-04 `/{page}/failed {reason}` | failures printed and discarded | `src/pipeline/run_speak_gemini.py:82` prints `f"{type(e).__name__}: {str(e)[:110]}"`; `tools/vlm_arbiter.py:101-109`; `tools/context_detector.py:126-131`; `walidapro/.../word_diacritics.py:1296` blanket-swallows into `REVIEW` | `MISSING` | There is no structured error object anywhere from which to build a `reason`. Worse, the blanket swallow turns an outage into a page that *reports success* with 100% `REVIEW`. | Error taxonomy + reason builder. `T-1.4`, `T-5.6` |
| REQ-CB-05 whole-job `/failed` | none | — | `MISSING` | `walidapro/run_full_pipeline.py` has terminal per-page statuses (`SUCCESS`/`REVIEW_REQUIRED`/`DIACRITICS_REVIEW_REQUIRED`/`FAILED`) but no whole-job failure concept and no exit-code contract. | `T-5.6` |

## REQ-CFG / REQ-ERR

| Requirement | Existing Implementation | Location | Status | Gap | Required Action |
|---|---|---|---|---|---|
| REQ-CFG-01 five envs from configuration | env-var config exists, but wrong shape | `walidapro/ocr_verifier/config.py:11-35` frozen `Settings` singleton evaluated at import; `load_dotenv()` at import in 11 modules; `src/`+`tools/` have no config object at all and use bare `os.environ[KEY]` subscripts in 7 places | `NEEDS REFACTOR` | None of the five V1 envs exists. And the existing pattern cannot be used in a worker: values are frozen at import, so credentials cannot rotate and thresholds cannot vary per job. `LIGHTON_BASE_URL` is additionally *misconfigured* — the default `http://localhost:8000/v1` plus the client's `/api/v3/parse` suffix builds `http://localhost:8000/v1/api/v3/parse`, and the variable is set in no `.env` in the repo. | `pydantic-settings`, validated at startup. `T-1.2` |
| REQ-ERR-01 `reason` on failures | none | — | `MISSING` | See REQ-CB-04. | `T-1.4` |

## Coverage summary

| Status | Count | Requirements |
|---|---:|---|
| `EXISTS` | 3 | REQ-AUTH-03 (vacuously), REQ-ART-01, REQ-ART-03 |
| `PARTIAL` | 7 | REQ-JOB-03, REQ-IDEM-01, REQ-IDEM-03, REQ-META-01, REQ-UPL-04, REQ-ART-02, REQ-ART-04 |
| `NEEDS REFACTOR` | 2 | REQ-ART-03 (dual narrator), REQ-CFG-01 |
| `MISSING` | 20 | all of REQ-AUTH (except -03), REQ-JOB-01/02/04/05, REQ-IDEM-02, all REQ-SRC, REQ-META-02, all REQ-UPL (except -04), all REQ-CB, REQ-ERR-01 |

**Every single integration requirement is `MISSING`. Every artifact requirement is at least `PARTIAL`.**
That asymmetry is the whole shape of the project: the AI is there, the plumbing is not.
