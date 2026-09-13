# Data and Storage Architecture

## 1. Storage categories

| Category | What | Where | Retention | Owner | Access pattern | Backup | Lifecycle |
|---|---|---|---|---|---|---|---|
| **Operational data** | `jobs`, `job_pages`, `artifacts`, `callback_outbox` | Postgres | **PROPOSAL** 90 days for terminal jobs, then archive-or-purge (`Q-12`) | AI Engine | Small indexed reads/writes; one claim query is the hot path | Daily snapshot + PITR | Purged by a scheduled job |
| **AI artifacts** | The four deliverables: image, text, audio, timing JSON | **The Backend's S3, via the upload cycle. The engine keeps only the `fileId`.** | Backend's policy | **Backend** | Write-once by us; never read back by us | Backend's | Backend's |
| **Files (source)** | The inbound PDF or free-text file | Scratch volume, transient | Deleted on every terminal path | AI Engine | Streamed in once, read many times during the job | **None** — always re-fetchable via REQ-SRC-02 | Deleted with the job's scratch dir |
| **Logs** | Structured JSON, one event per line | stdout → the host's collector | Collector's policy, **PROPOSAL** 30 days | AI Engine | Search by `job_id` | Collector's | — |
| **Metrics** | Prometheus counters/histograms/gauges | In-process, scraped from `/metrics` | Scraper's policy, **PROPOSAL** 15 months | AI Engine | Dashboard + alert queries | Scraper's | — |
| **Temporary data** | Rendered page images, PCM buffers, chunk WAVs, crops, per-word diacritics caches | Scratch volume, `/var/tmp/ai-engine/{job_id}/` | Deleted at page and job completion | AI Engine | Sequential, single-writer | None | Reconciler sweeps orphans |
| **Model weights** | faster-whisper `small` int8, PaddleOCR PP-OCRv5, the diacritizer BiLSTM | **Persistent** cache volume | Indefinite | AI Engine | Read-only after first load | Re-downloadable | Warmed at startup |

## 2. The engine owns no durable artifact store

**Decision: artifacts go to the Backend's storage and the engine retains only references.**
([ADR-006](19-adrs.md))

Why this is right:

- **Single owner.** The Backend already has the bucket, the lifecycle policy, the CDN and the file
  records — the whole upload cycle in REQ-UPL exists precisely to put files *there*. A second copy in an
  engine-owned bucket would immediately be a divergence risk and a second thing to secure and pay for.
- **CONSTRAINT C2** is explicit about it.
- It keeps the engine stateless in the dimension that matters. A worker holds nothing that another
  worker could not reconstruct.

What it costs us, stated plainly:

- **We cannot re-serve an artifact.** If the Backend loses a file, we must re-run the job — which means
  re-paying for the AI. There is no cheap "just re-upload it" path.
- **We cannot diff a re-run against the previous output** without downloading from the Backend.
- Debugging a "this audio sounds wrong" report requires either the Backend's copy or a local re-run.

That trade is correct for V1 and is worth revisiting if artifact-level regression testing becomes a
requirement.

## 3. Postgres schema

```mermaid
erDiagram
    JOBS ||--o{ JOB_PAGES : "fans out into"
    JOBS ||--o{ ARTIFACTS : "produces"
    JOBS ||--o{ CALLBACK_OUTBOX : "emits"
    JOB_PAGES ||--o{ ARTIFACTS : "each page has 2"

    JOBS {
        uuid job_id PK
        text job_type
        text state
        text file_id
        text folder_id
        text path_prefix
        int page_number "Job B only"
        text quality_tier
        int pages_count "null until counted"
        timestamptz metadata_sent_at
        text payload_hash "for the 409 check"
        int attempt
        text claimed_by
        timestamptz lease_until
        text failure_code
        text failure_reason
    }
    JOB_PAGES {
        uuid job_id PK_FK
        int page_number PK
        text state
        int attempt
        timestamptz lease_until
        text failure_code
        text failure_reason
    }
    ARTIFACTS {
        uuid job_id PK_FK
        int page_number PK
        text artifact_type PK
        text backend_file_id
        text content_type
        bigint size_in_bytes
        timestamptz confirmed_at
    }
    CALLBACK_OUTBOX {
        bigserial id PK
        uuid job_id FK
        int page_number
        text kind
        jsonb payload
        int attempt
        timestamptz next_attempt_at
        timestamptz delivered_at
    }
```

```sql
CREATE TYPE job_type   AS ENUM ('PAGES_IMG_AND_TEXT_EXTRACTION',
                                'PAGE_AUDIO_AND_TEXT_TIMING_GENERATION');
CREATE TYPE job_state  AS ENUM ('QUEUED','RUNNING','COMPLETED','FAILED','DEAD_LETTER');
CREATE TYPE page_state AS ENUM ('PENDING','RUNNING','ARTIFACTS_UPLOADED',
                                'REPORTED_COMPLETED','REPORTED_FAILED');
CREATE TYPE artifact_type AS ENUM ('Image','Text','Audio','JSON');   -- exact REQ-CB-02/03 wire values
CREATE TYPE callback_kind AS ENUM ('metadata','page_completed','page_failed','job_failed');
CREATE TYPE quality_tier  AS ENUM ('fast','verified');

-- ─── jobs: the idempotency store (REQ-JOB-05, REQ-IDEM-02) ────────────────────
CREATE TABLE jobs (
    job_id            uuid        PRIMARY KEY,          -- THE idempotency key (REQ-IDEM-01)
    job_type          job_type    NOT NULL,
    state             job_state   NOT NULL DEFAULT 'QUEUED',
    file_id           text        NOT NULL,
    folder_id         text        NOT NULL,
    path_prefix       text        NOT NULL,
    page_number       int         NULL,                 -- Job B only
    quality_tier      quality_tier NOT NULL DEFAULT 'fast',
    pages_count       int         NULL,                 -- NULL until counted
    metadata_sent_at  timestamptz NULL,                 -- gates all page work (REQ-META-01)
    payload_hash      text        NOT NULL,             -- canonical hash, excludes sourceDownloadUrl
    attempt           int         NOT NULL DEFAULT 0,
    claimed_by        text        NULL,
    lease_until       timestamptz NULL,
    failure_code      text        NULL,
    failure_reason    text        NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT job_b_has_page CHECK (
        (job_type = 'PAGE_AUDIO_AND_TEXT_TIMING_GENERATION' AND page_number IS NOT NULL)
     OR (job_type = 'PAGES_IMG_AND_TEXT_EXTRACTION'         AND page_number IS NULL)),
    CONSTRAINT pages_count_positive CHECK (pages_count IS NULL OR pages_count > 0),
    CONSTRAINT failed_has_reason CHECK (state <> 'FAILED' OR failure_reason IS NOT NULL)
);

-- the queue-claim hot path
CREATE INDEX jobs_claimable ON jobs (created_at) WHERE state = 'QUEUED';
-- the lease-recovery sweep
CREATE INDEX jobs_expired_lease ON jobs (lease_until) WHERE state = 'RUNNING';
-- the retention purge
CREATE INDEX jobs_terminal_age ON jobs (updated_at)
    WHERE state IN ('COMPLETED','FAILED','DEAD_LETTER');

-- ─── job_pages: the INVARIANT-1 ledger ────────────────────────────────────────
CREATE TABLE job_pages (
    job_id         uuid        NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    page_number    int         NOT NULL,
    state          page_state  NOT NULL DEFAULT 'PENDING',
    attempt        int         NOT NULL DEFAULT 0,
    lease_until    timestamptz NULL,
    failure_code   text        NULL,
    failure_reason text        NULL,
    started_at     timestamptz NULL,
    terminal_at    timestamptz NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (job_id, page_number),                  -- one row per page, exactly
    CONSTRAINT page_number_positive CHECK (page_number >= 1),
    CONSTRAINT terminal_has_timestamp CHECK (
        (state IN ('REPORTED_COMPLETED','REPORTED_FAILED')) = (terminal_at IS NOT NULL)),
    CONSTRAINT reported_failed_has_reason CHECK (
        state <> 'REPORTED_FAILED' OR failure_reason IS NOT NULL)
);

CREATE INDEX job_pages_claimable ON job_pages (job_id, page_number) WHERE state = 'PENDING';
CREATE INDEX job_pages_nonterminal ON job_pages (job_id)
    WHERE state NOT IN ('REPORTED_COMPLETED','REPORTED_FAILED');
CREATE INDEX job_pages_expired_lease ON job_pages (lease_until) WHERE state = 'RUNNING';

-- ─── artifacts: makes upload idempotent (REQ-UPL) ─────────────────────────────
CREATE TABLE artifacts (
    job_id          uuid          NOT NULL,
    page_number     int           NOT NULL,
    artifact_type   artifact_type NOT NULL,
    backend_file_id text          NOT NULL,
    content_type    text          NOT NULL,
    size_in_bytes   bigint        NOT NULL,
    confirmed_at    timestamptz   NOT NULL DEFAULT now(),

    -- a retried page skips whatever already confirmed
    PRIMARY KEY (job_id, page_number, artifact_type),
    FOREIGN KEY (job_id, page_number) REFERENCES job_pages(job_id, page_number) ON DELETE CASCADE,
    CONSTRAINT content_type_allowed CHECK (
        content_type IN ('image/jpeg','application/json','audio/wav','text/plain')),  -- REQ-UPL-04
    CONSTRAINT size_positive CHECK (size_in_bytes > 0)
);

-- ─── callback_outbox: makes callbacks survive worker death ────────────────────
CREATE TABLE callback_outbox (
    id              bigserial     PRIMARY KEY,
    job_id          uuid          NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    page_number     int           NULL,                 -- NULL for metadata / job_failed
    kind            callback_kind NOT NULL,
    payload         jsonb         NOT NULL,
    attempt         int           NOT NULL DEFAULT 0,
    next_attempt_at timestamptz   NOT NULL DEFAULT now(),
    delivered_at    timestamptz   NULL,
    last_error      text          NULL,
    created_at      timestamptz   NOT NULL DEFAULT now(),

    -- at most one row per logical callback: prevents a double completed for one page
    CONSTRAINT one_callback_per_target UNIQUE (job_id, page_number, kind)
);

CREATE INDEX outbox_pending ON callback_outbox (next_attempt_at)
    WHERE delivered_at IS NULL;
```

### The queue-claim query

```sql
-- claim one job; SKIP LOCKED lets N workers claim concurrently without blocking
UPDATE jobs SET state = 'RUNNING',
                attempt = attempt + 1,
                claimed_by = $1,
                lease_until = now() + ($2 || ' seconds')::interval,
                updated_at = now()
WHERE job_id = (
    SELECT job_id FROM jobs
    WHERE state = 'QUEUED' AND attempt < $3
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING *;
```

```sql
-- the INVARIANT-1 check, and the reconciler's core query
SELECT j.job_id,
       j.pages_count,
       count(*) FILTER (WHERE p.state NOT IN ('REPORTED_COMPLETED','REPORTED_FAILED')) AS non_terminal
FROM jobs j JOIN job_pages p USING (job_id)
WHERE j.state = 'RUNNING' AND j.updated_at < now() - interval '1 hour'
GROUP BY j.job_id, j.pages_count
HAVING count(*) FILTER (WHERE p.state NOT IN ('REPORTED_COMPLETED','REPORTED_FAILED')) > 0;
```

The `one_callback_per_target` unique constraint is worth dwelling on: it is what makes it *structurally
impossible* to enqueue two `page_completed` callbacks for the same page, no matter how many times a page
task re-runs. Belt to the outbox's braces.

## 4. Sizing

All artifact sizes are **measured** from real files on disk (119 JPEGs, 95 WAVs, 186 page records).

### Per page

| Artifact | Median | p90 | Max observed |
|---|---:|---:|---:|
| `Image` (JPEG, max edge 1100 px, q82) | 161 KB | 255 KB | 503 KB |
| `Text` (UTF-8) | 643 B | ~1.5 KB | ~4 KB |
| `Audio` (WAV 24 kHz mono 16-bit) | 1,018 KB | 1,824 KB | **8,870 KB** |
| `JSON` (timing projection) | ~3.3 KB | ~6 KB | ~15 KB |
| **Total uploaded per page** | **~1.18 MB** | **~2.09 MB** | **~9.4 MB** |

### Per book

Using `README.md:182`'s measured ratio of **31.4 pages/book**:

| | Median book | p90 book |
|---|---:|---:|
| Uploaded to the Backend | **~37 MB** | ~66 MB |
| Of which audio | ~32 MB (86%) | ~57 MB |

The audio dominates by an order of magnitude, which is why `Q-09` (WAV vs a compressed codec) is worth
asking. `README.md:186-190`'s own scale table puts 1,000 books at **36 GB WAV versus 12 GB MP3**.

### Scratch high-water mark

Scratch holds only what is in flight, not the whole book:

```text
per concurrent page task:
  rendered page image, decoded RGB @ 220 DPI   ~14 MB   (1863x2573x3)
  encoded JPEG                                 ~0.2 MB
  PCM audio buffer                             ~1.3 MB  (median), 8.9 MB worst
  timing + text                                ~0.01 MB
                                             ---------
                                              ~16 MB typical, ~24 MB worst

per job:  source PDF (a few hundred KB in the sample corpus; uncapped by the spec — Q-08)

worker host at defaults (WORKER_JOB_CONCURRENCY=2, WORKER_PAGE_CONCURRENCY=4):
  2 jobs x source + 4 x 24 MB  ≈  100 MB
```

**PROPOSAL: a 5 GB scratch volume per worker.** That is ~50× the computed high-water mark, which buys
headroom for an uncapped source PDF and for the verified tier's crop directories — `cropper.py:205`
writes a word crop, a 4×-padded context crop and 5 variants **per suspicious word**, which is
unbounded today.

Critically, this design does **not** replicate the existing behaviour at
`walidapro/ocr_verifier/pdf_render.py:41-107`, which materializes every selected page as a PIL image in
one list — ~14 MB of RGB per page, so ~1.4 GB for a 100-page book, all before any work begins.
A streaming renderer is a prerequisite (`T-3.1`).

### Database growth

| Rows per Job A (31.4 pages) | |
|---|---:|
| `jobs` | 1 |
| `job_pages` | 31 |
| `artifacts` | 63 |
| `callback_outbox` | 33 |
| **Total** | **~128 rows/book** |

At 100 books/day that is ~13 K rows/day, ~4.7 M rows/year — measured in **megabytes**, not gigabytes.
This is worth stating explicitly so nobody partitions, shards, or reaches for a "scalable" datastore.
Postgres is not the constraint here and will not be for years. See [ADR-005](19-adrs.md).

## 5. Scratch workspace design

```text
/var/tmp/ai-engine/
  {job_id}/                         # one directory per job, UUID-named
    source.pdf | source.txt         # the fetched input
    p007/                           # one subdirectory per page task
      page.jpg
      page.txt
      page.wav
      page.timing.json
      _tmp/                         # verified-tier crops, chunk WAVs, per-word caches
```

Rules:

| Rule | Why |
|---|---|
| Directory name is the **`job_id` UUID** | Not the PDF filename stem. `src/pipeline/main.py:62-63` uses `work/{pdf_path.stem}` and the real directories on disk are named with Arabic text, spaces, `?` and `' - Copy'` — unusable as an identity, unsafe as a path, and non-unique across jobs. |
| Deleted on **every** terminal path | `COMPLETED`, `FAILED` and `DEAD_LETTER` all clean up. |
| Page subdirectory deleted at page terminal | Bounds the high-water mark to in-flight pages, not the book. |
| Orphans swept by the reconciler | A SIGKILL leaves a directory; the sweep matches directories against terminal jobs. |
| **Never reused across jobs** | Two jobs over the same book get two directories. The current code's shared-directory model is exactly what makes concurrent runs corrupt each other. |
| All writes atomic | tmp-file + `os.replace`. `walidapro/ocr_verifier/utils.py:48 atomic_write_text` already implements this — promote it rather than writing a new one. |

## 6. Model weights cache

| Model | Size | Source | Notes |
|---|---:|---|---|
| faster-whisper `small`, int8 | **464 MB (measured)** at `~/.cache/huggingface/hub/models--Systran--faster-whisper-small` | HuggingFace, on first use | `src/pipeline/align_whisper.py:19-23`, `device="cpu"`, `compute_type="int8"`, hardcoded |
| PaddleOCR PP-OCRv5 mobile det + `arabic_PP-OCRv5_mobile_rec` | tens of MB | PaddleOCR's own fetcher, on first use | `walidapro/ocr_verifier/bbox_detector.py:44,47`. Verified tier only. **Loaded twice today** — `pipeline.py:44` and `word_diacritics.py:93`. |
| `arabic-diacritizer` BiLSTM | small | `Diacritizer.from_pretrained()` | `src/pipeline/tashkel.py:31` |

**PROPOSAL: mount a persistent named volume** at the HuggingFace and PaddleOCR cache paths, shared by all
worker replicas.

Without it, every cold container downloads 464 MB before it can serve a single page. Measured cold-load
costs: `import pipeline.align_whisper` = **2.56 s**, `_get_model()` from a warm disk cache = **1.54 s** —
and that is *after* the download. Readiness must wait for the warm-up
([07-api-specification.md](07-api-specification.md) §1.3), so without the volume every deploy is a
multi-minute, bandwidth-bound event.

## 7. Data-integrity practices the existing code lacks

| Practice | Current state | Requirement for the service |
|---|---|---|
| **Atomic writes** | `grep -rnE 'os\.replace\|\.rename\(\|fsync\|flock'` over `src/pipeline` and `tools` returns **zero matches**. `tools/context_detector.py:17-20` records that `ensemble_review.json` "already nearly lost real `vlm_arbiter.py` work to a careless write once tonight". | Every artifact and cache write is tmp-file + `os.replace`. Reuse `walidapro/ocr_verifier/utils.py:48`. |
| **No absolute host paths in persisted data** | `ensemble_review.json` and `context_review.json` store `gemini_dir`/`lighton_dir` as absolute host paths, read back at `tools/vlm_arbiter.py:85-86` via `Path(review['gemini_dir'])`. | Nothing we persist or upload contains a host path. The `artifacts` table stores the Backend's `fileId`, nothing local. |
| **Locking / single-writer** | None. `walidapro/.../word_diacritics.py:1143`→`:1405` is a lock-free whole-file read-modify-write with a network call in the middle — two workers sharing a directory lose each other's entries. | Per-job scratch isolation makes locking unnecessary. Postgres row locks handle the shared state. |
| **Bounded writes** | `cropper.py:205` writes 7 PNGs per suspicious word with no cap. | Scratch quota per page task; verified tier writes into `_tmp/` which is deleted at page terminal. |

## 8. Retention, PII, and re-runs

The content is **Arabic children's book pages** — published material, not user-generated content and
not personal data. Concretely:

- The artifacts contain no personal data. The `Text` and `JSON` artifacts are book text and timings.
- The operational data contains no personal data either: job ids, file ids, folder ids, page numbers,
  error codes. **No inbound token, no `client_secret` and no presigned URL is ever persisted** —
  REQ-AUTH-03 forbids the token explicitly, and presigned URLs are credentials
  ([12-security.md](12-security.md)).
- `failure_reason` is redacted at construction, so it cannot carry a URL or a key into the database.

**PROPOSAL** (all `Q-12`, none stated by the PDF):

| | Default |
|---|---|
| Terminal `jobs` rows and their children | 90 days, then purge |
| `callback_outbox` delivered rows | 7 days, then purge |
| Logs | 30 days |
| Metrics | 15 months |
| Scratch | minutes |
| Re-run of the same book | A new `jobId` is a new job. The engine does **not** deduplicate by content — two jobs over the same PDF produce two independent sets of Backend files. Whether the Backend wants that, or wants us to detect it, is `Q-12`. |
