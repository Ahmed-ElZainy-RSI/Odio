# Gap Analysis

Interpretation of [03-requirements-to-code-map.md](03-requirements-to-code-map.md). The row-by-row map
tells you *what* is missing; this document tells you *what kind* of missing it is, because the five kinds
have wildly different costs.

## 1. Gap taxonomy

| Bucket | Meaning | Cost character |
|---|---|---|
| **A — Protocol gaps** | Nothing exists. Pure new build, but well-understood work with no research risk. | Predictable |
| **B — Shape gaps** | The capability exists and works, but its execution model is wrong for a long-lived service. | Risky — touching working AI code |
| **C — Artifact gaps** | The data is already computed; it is simply never written as a file. | Cheap |
| **D — Quality & observability gaps** | We cannot see or measure what the system does. | Cheap but blocking |
| **E — Engineering-hygiene gaps** | The project cannot be built, installed, or deployed reproducibly. | Cheap and must come first |

### Bucket A — Protocol gaps (pure build)

| Gap | Size | Blast radius | Notes |
|---|---|---|---|
| HTTP surface (`POST /api/jobs`, health, metrics) | S | New code only | One endpoint plus probes. FastAPI + pydantic makes this small. |
| Inbound JWT validation | M | New code only | Small in code, but **blocked on `Q-01`** — the PDF never says how to validate. |
| Keycloak `client_credentials` token provider | S | New code only | ~80 lines. Needs single-flight refresh so N workers do not stampede. |
| Job store + idempotent accept | S | New code only | One table, one `ON CONFLICT DO NOTHING`. |
| Queue + worker runtime | M | New code only | Postgres `FOR UPDATE SKIP LOCKED`. See [ADR-004](19-adrs.md). |
| Source fetch + expired-URL refresh | S | New code only | Two HTTP calls and a retry rule. |
| Three-step upload cycle | M | New code only | Ordering and idempotency matter more than the HTTP. |
| Callback client + durable outbox | M | New code only | The outbox is what makes INVARIANT-1 survivable. |
| Reconciler | M | New code only | Sweeps for jobs whose pages are not all terminal. |
| **Total bucket A** | **~2,500–3,500 LOC of new service code** | | No AI risk. This is ordinary backend work. |

### Bucket B — Shape gaps (the risky ones)

These are the gaps where we must modify code that currently works.

| Gap | Evidence | Size | Blast radius |
|---|---|---|---|
| **Book loops → per-page tasks.** Every driver owns its own `for page in …` loop with no hook. | `src/pipeline/run_ocr_gemini.py:44`, `run_extract.py:42`, `run_ocr_lighton.py:50`, `run_speak_gemini.py:78`, `run_tashkel.py:36` | L | Touches all 7 drivers. The 56 tests must stay green. |
| **Fail-fast → per-page isolation.** `break`/`return` on any exception abandons all remaining pages. | `run_ocr_gemini.py:59-62`, `run_speak_gemini.py:79-84`, `run_ocr_lighton.py:58`, `run_speak_hume.py:63-65` | M | Same 7 drivers. Directly gates INVARIANT-1. |
| **Module-global model singletons.** Unguarded lazy globals; concurrent first-touch double-builds. | `src/pipeline/align_whisper.py:16-23`, `src/pipeline/tashkel.py:25-33` | S | Two files, but every caller. |
| **`SystemExit` from library code.** 19 sites; `BaseException`, so it escapes `except Exception` and kills a worker. Two tests *assert* it. | `tools/ensemble_review.py:133` (inside `run()`), `src/tests/test_ensemble_review.py:143`, `test_apply_context_suggestions.py:105` | M | Requires changing tests too — do it deliberately. |
| **Import-time `load_dotenv()`** in 11 modules mutates process env. | `src/pipeline/run_ocr_gemini.py:20`, `walidapro/ocr_verifier/config.py:7`, `final_context_reviewer.py:14`, +8 | S | Wide but mechanical. |
| **Frozen config singleton.** `SETTINGS` evaluated once at class definition; no per-job override. | `walidapro/ocr_verifier/config.py:11-35` | M | All of `walidapro/`. |
| **Per-page subprocess fork.** Reloads the interpreter and PaddleOCR per page, returns only an exit code, no timeout. | `walidapro/run_full_pipeline.py:503-529` | M | Verified tier only, so deferrable. |
| **All pages materialized in RAM.** `render_pdf` returns a full list of PIL images — ~14 MB RGB/page at 220 DPI. | `walidapro/ocr_verifier/pdf_render.py:41-107`, called for the whole selection at `pipeline.py:1828` | S | Add a generator variant; the function is otherwise clean. |
| **Blocking `time.sleep` retries with no jitter and no `Retry-After`.** Worst case 546 s of blocked worker per call. | `walidapro/ocr_verifier/lighton_ocr.py:197`, `reviewer.py:284`, `visual_diacritics.py:655`, `tts/gemini_tts.py:707` | M | Replace with a shared retry policy. |
| **Double-retry amplification.** App retries 3× while the OpenAI SDK independently retries 2× → up to 9 billed completions for one page. | `walidapro/ocr_verifier/final_context_reviewer.py:807`/`:251` vs the SDK default `max_retries=2` never overridden at `:267`/`:825` | S | One-line fix, real money. |
| **No timeouts.** `final_context_reviewer.py:825` passes none, so the SDK's 600 s read default applies. | ibid. | S | |
| **Non-atomic writes, no locking.** Zero matches for `os.replace`/`rename`/`fsync`/`flock` in `src/pipeline` and `tools`. | see search in `03` | S | `walidapro/ocr_verifier/utils.py:48 atomic_write_text` already has the right pattern — reuse it. |
| **Sequential per-word diacritics loop.** One VLM call per word, strictly serial. | `walidapro/.../word_diacritics.py:1611-1660` | L | Verified tier only. Must be parallelized before that tier is usable. |

### Bucket C — Artifact gaps (cheap, high value)

| V1 artifact | Status | What is actually missing | Measured size | Size |
|---|---|---|---|---|
| `Image` | **EXISTS** | Only that the LightOn driver never creates `pages/` (`src/pipeline/run_ocr_lighton.py` has no `mkdir` for it). | median 161 KB, p90 255 KB | S |
| `Text` | **Computed, then discarded** | One line. `text = " ".join(t["txt"] for t in tokens)` at `src/pipeline/run_ocr_gemini.py:65` is a local variable that is never written to disk. | median 643 B | **XS** |
| `Audio` | **EXISTS** | Nothing, on the Gemini path. The Hume path emits MP3, which is not in the V1 content-type enum. | median 1.0 MB, p90 1.8 MB, max 8.9 MB | S |
| `JSON` (timing) | **Exists as data, not a file** | A projection. Timings are `tokens[].t` / `t_src` inside the mixed-purpose `pNNN.json`, which also carries OCR provenance and the raw `whisper_words` array (21% of the record by bytes). | ~3.3 KB projected from an 8.1 KB record | S |

**Two of the four mandated artifacts are one afternoon of formatting work, not AI work.** This is the
single most reassuring finding in the analysis and it should be said early and often — see
[00-executive-summary.md](00-executive-summary.md).

The one genuine capability constraint sits behind the `JSON` row: **Pipeline W cannot produce timings at
all.** A repo-wide search for `timing|timestamp|word_time|highlight|srt|vtt` across every
`walidapro/**/*.py` returns zero hits. That single fact forces Job B onto Pipeline S
([ADR-009](19-adrs.md)).

### Bucket D — Quality and observability gaps

| Gap | Evidence | Size |
|---|---|---|
| **Zero timing instrumentation.** No `perf_counter`, no elapsed logging, no report file with wall-clock numbers anywhere. `time` is imported only for `sleep`. | repo-wide | S |
| **No structured logging.** Bare `print()` throughout — ~20 calls in `final_context_reviewer.py` alone (`:731`, `:770-800`, `:896-1010`). Concurrent pages would interleave unattributably. | | S |
| **No metrics, no health checks, no correlation ids.** | | S |
| **No cost visibility.** No page-level or job-level call budget or counter anywhere; `word_diacritics.py` has no per-page call cap. | | S |
| **Quality signal exists but is unused.** `bind_report` (`anchored`/`interpolated`/`untimed`/`monotonic`) and `FinalReviewResult.review_required` are exactly the right telemetry and are written to disk, then never aggregated. | `src/pipeline/run_speak_gemini.py:52-57` | XS |

This bucket is cheap but **blocking**: every latency figure in this document set is an estimate
precisely because of the first row, and you cannot enforce a page deadline you cannot measure.

### Bucket E — Engineering-hygiene gaps

| Gap | Evidence | Size |
|---|---|---|
| **Not installable.** No `pyproject.toml`/`setup.py`/`setup.cfg`; no `__init__.py` in `src/`, `src/pipeline/`, `tools/`, `src/tests/`. Imports resolve only via PEP-420 namespace packages plus `sys.path.insert` hacks (`src/tests/conftest.py:18-21`). | | S |
| **Dependency manifest 1/14 correct.** `requirements.txt` is 15 bytes: `faster_whisper`. Real third-party imports: `pypdfium2`, `fitz`/`pymupdf`, `pypdf`, `PIL`, `numpy`, `requests`, `dotenv`, `openai`, `google`, `hume`, `paddleocr`, `faster_whisper`, `diacritize`, `pytest`. Zero version pins. | | S |
| **No container, no CI, no build.** No `Dockerfile`, `docker-compose`, `.github/`, `Makefile`, `tox.ini`, `.pre-commit-config.yaml` — and none ever existed in git history. A stale `.ruff_cache/0.12.7/` exists but there is no ruff config and ruff is not installed. | | M |
| **Live credentials in the repo.** `.env` at the repo root holds real values for 8 vendor keys, and `load_dotenv()` at import in 11 modules means a mounted `.env` silently overrides container-injected secrets. | | S |
| **Artifacts carry absolute host paths.** `ensemble_review.json` persists `gemini_dir`/`lighton_dir`; `tools/vlm_arbiter.py:85-86` reads them back with `Path(review['gemini_dir'])`. Not portable across containers. | | S |
| **740 KB `SESSION.md` and ~1,400 LOC of abandoned `tmp/` spikes** in the tree. | | XS |

## 2. Reuse ledger

Verdict per component, with the arithmetic. LOC is a crude proxy — it over-weights `walidapro/`, which
is 83% of production Python — but it is honest and checkable.

| Verdict | LOC | % of 19,540 | Components |
|---|---:|---:|---|
| **REUSE_AS_IS** | 1,996 | 10% | `src/pipeline`: `extract.py` (105), `pages.py` (30), `speak.py` (115), `align_whisper.py` (33). `tools`: `ensemble_review.py` (189), `apply_corrections.py` (131), `apply_context_suggestions.py` (102), `replace_page.py` (93) — for their merge/alignment logic. `walidapro`: `decision.py` (500), `models.py` (137), `utils.py` (52), `pdf_render.py` (109), `cropper.py` (312), `tts/models.py` (88). |
| **WRAP** | 6,237 | 32% | `src/pipeline`: `ocr_gemini.py` (127), `ocr_lighton.py` (100), `tashkel.py` (56), `tts_gemini.py` (39), `tts_hume.py` (25). `walidapro`: `reviewer.py` (831), `visual_reviewer.py` (973), `final_context_reviewer.py` (1,092), `lighton_ocr.py` (408), `bbox_detector.py` (260), `tts/gemini_tts.py` (714), `tts/text_chunker.py` (469), `tts/audio_manager.py` (383), `tts/pipeline.py` (760). |
| **REFACTOR** | 5,539 | 28% | `walidapro`: `ocr_verifier/pipeline.py` (2,237 — keep `process_page`, discard `run_pdf`/`save_outputs`), `diacritization/word_diacritics.py` (1,783), `diacritization/visual_diacritics.py` (871), `diacritization/rebuild_text.py` (648). |
| **REPLACE** | 3,597 | 18% | `src/pipeline`: `main.py` (126) and all 6 `run_*.py` drivers (534). `walidapro`: `run_full_pipeline.py` (2,911), `run.py` (26). |
| **DROP from the service path** | 2,136 | 11% | `src/pipeline/build_reader.py` (343). `tools`: `build_review.py` (372), `build_context_review.py` (189), `context_detector.py` (180), `vlm_arbiter.py` (137), `run_full_pipeline.py` (139), `export_session.py` (220). `walidapro`: `pdf_type_detector.py` (158, verified dead), and 4 ad-hoc test scripts (398). |

Rolling that up:

- **Reusable with little or no change (REUSE_AS_IS + WRAP): 8,233 LOC = 42%.**
- **Salvageable including refactor: 13,772 LOC = 71%.**
- **Discarded (REPLACE + DROP): 5,733 LOC = 29%** — and note that 51% of the discarded total is one
  file, `walidapro/run_full_pipeline.py`, whose *stage functions* survive; only its CLI orchestration
  layer dies.

The important qualitative point that LOC cannot express: **the discarded 29% is all orchestration, and
the retained 71% is all domain logic.** Nothing about Arabic OCR, diacritization, TTS or alignment is
being reimplemented. That satisfies Rule 2 of the parent brief — do not rewrite working AI pipelines to
fit a preferred architecture.

Note also that the four items marked `DROP` from `tools/` are **not** dead code and are not being
deleted from the repository. They are *deliberately human-gated* — `tools/context_detector.py:148-160`
seeds `context_decisions.json` with every entry defaulting to `"reject"`, and
`tools/run_full_pipeline.py:99-115` prints *"STOPPING HERE ON PURPOSE -- one manual step left"*. They
cannot live inside an automated job, and they remain valuable as an offline quality tool. Their absence
from V1 is a genuine quality reduction, recorded as `R-11` in
[23-risks-and-tradeoffs.md](23-risks-and-tradeoffs.md).

## 3. What would break first

If someone put FastAPI in front of the existing code this afternoon and POSTed a job, here is the
failure sequence, in order:

| # | Failure | Cause |
|---|---|---|
| 1 | **`ImportError` at startup.** | No packaging, no `__init__.py`, imports depend on `sys.path.insert` hacks (`src/tests/conftest.py:18-21`) and a CWD that happens to be right. `walidapro/run.py` only resolves `ocr_verifier` if the CWD is `walidapro/`. |
| 2 | **`SystemExit` kills the worker, not the request.** | 19 sites, e.g. `tools/ensemble_review.py:133` raising from inside `run()`. `SystemExit` inherits `BaseException` and escapes `except Exception`. |
| 3 | **The event loop stalls for minutes.** | Every provider call is a blocking `requests.post`, and every retry is `time.sleep` (`walidapro/ocr_verifier/lighton_ocr.py:197` — worst case 546 s). `src/pipeline/run_speak_hume.py:77` wraps itself in `asyncio.run()`, so calling it from an async handler raises `RuntimeError: asyncio.run() cannot be called from a running event loop`. |
| 4 | **The second concurrent job corrupts the first.** | Identity is `work/{pdf_path.stem}` (`src/pipeline/main.py:62-63`) with no locking, and `walidapro/ocr_verifier/config.py:35`'s frozen `SETTINGS` singleton means both jobs share one model, one threshold set and one credential. Two workers sharing a diacritics cache directory lose each other's entries via the lock-free whole-file rewrite at `word_diacritics.py:1143→:1405`. |
| 5 | **The book stalls in the Backend forever.** | The first page error hits `break` (`src/pipeline/run_ocr_gemini.py:59-62`) and every remaining page never reports. INVARIANT-1 violated; the Backend waits for page N indefinitely. |

Every one of these is addressed in Phases 0–4 of
[21-implementation-roadmap.md](21-implementation-roadmap.md), and #5 is the reason the reconciler exists
at all ([09-async-jobs.md](09-async-jobs.md)).
