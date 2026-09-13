# Extending the service

The document to read if you are implementing the next piece.

## The pattern

Every capability enters through a **port** — a `Protocol` in `ai_engine/ports/` with a
docstring that states the contract — and is implemented by an **adapter** in
`ai_engine/adapters/`. Adapters are the only layer permitted to import a provider SDK or
the legacy `src/pipeline` code.

```
application/  ──uses──▶  ports/  ◀──implements──  adapters/
                  │
                  └── the contract lives in the port's docstring
```

Three rules make this hold:

1. `domain/` imports only the standard library.
2. `api/` never imports an adapter.
3. `application/` receives adapters by injection, never by import.

All three are asserted by tests in `tests/unit/test_domain_purity.py`. If you break one,
the suite tells you.

## Implementing a port

### 1. Read the contract

The port's docstring is normative. It says what to return, what to raise, and — usually
most importantly — **what counts as degradation rather than failure**. For example
`OcrEngine`:

> An illustration-only page returns an EMPTY sequence, NOT an error. The Backend still
> waits for that page, so it must be reportable as completed.

That distinction is the difference between a working book and a stalled one, and it is not
obvious from the type signature.

### 2. Write the adapter

Look at `adapters/ai/ocr_textlayer.py` as the reference: it is 86 lines, wraps existing
legacy code, translates the legacy dict into domain `Token` objects, and does nothing else.

Every adapter must:

| Requirement | Why |
|---|---|
| Raise typed domain errors (`AiInferenceError`, `ModelUnavailableError`, …) | The page executor classifies by error code to build a safe `reason` |
| **Never** raise `SystemExit` | It is a `BaseException`, so it escapes `except Exception` and kills the worker instead of failing one page. There are 19 such sites in the legacy tree. |
| **Never** blanket-swallow into a plausible-looking result | `word_diacritics.py:1296` turns a provider outage into a page of 100% `REVIEW` that still *reports success*. Distinguish "the model is uncertain" from "the provider is broken". |
| Take an explicit timeout | `final_context_reviewer.py:825` passes none, so the SDK's 600 s default applies and one hung call pins a worker for ten minutes |
| Set its SDK's own retry count to 0 | Three layers already retry: the Backend, us, and the SDK. They multiply. |
| Accept injected config, not module globals | A frozen import-time singleton cannot pick up a rotated credential |
| Not hold a module-global model | `align_whisper.py:16-23` and `tashkel.py:25-33` use unguarded lazy globals that double-build under concurrent first touch |

### 3. Wire it in `worker.py`

`worker.py` is the composition root — the only module that knows concrete types. Add the
construction there and pass it into the application object that needs it.

### 4. Test it

- A unit test with a fake collaborator, asserting the contract's edge cases (empty page,
  provider error, degradation).
- An e2e test through `FakeBackend`, asserting call order if the change touches the
  protocol.

## Worked example: the paid OCR adapters (`T-3.3`)

Three engines implement one port. The interesting part is not the HTTP call — it is that
the *choice* is made per document, and that each engine tells the truth about what it can
produce.

```python
# ai_engine/adapters/ai/ocr_providers.py
class GeminiVisionOcr:
    name = "gemini"
    provider = "gemini"          # which budget this engine spends

    def extract_page(self, *, page_image, pdf_path, page_number):
        records = legacy.lines_to_tokens(lines, page_number, extract.clean_word)
        return tokens, PageProvenance(ocr_engine=self.name, box_level="line", ...)
```

`box_level` is the honesty field. Gemini returns **per-line** boxes, which the legacy
`lines_to_tokens` subdivides into per-word boxes by character width — so the geometry is
approximate and says so. LightOn returns markdown with no geometry at all, so its adapter
**omits** `box` rather than fabricating one, and reports `box_level="none"`. A consumer
that draws a highlight box needs to know which of those it has.

Two rules the adapters follow, both of which came from operating the legacy pipeline:

**Tokenisation is not reimplemented.** `lines_to_tokens`, `text_to_tokens`, `clean_markdown`
and `clean_word` are called from `src/pipeline` verbatim. Job B reproduces Job A's token ids
from the text file alone; a second tokenizer that differed by one character would silently
break that hinge.

**A missing key is refused at selection, not at the call.** `build_ocr_engine` raises
`ModelUnavailableError` when the chosen provider has no credential, so a misconfigured
deployment fails the job **once**, before any page is rendered — instead of failing all 200
pages after paying to render each one.

### Selection happens per document

```python
def ocr_for(pdf_path: str) -> object:  # worker.py
    """`auto` inspects THIS PDF's text layer, so the answer is per document."""
```

`auto` prefers the embedded text layer when the document has one: exact, free, and 25 of the
36 sample books qualify. The default should never spend money it does not have to. The
resolved engine is cached per source path — a 200-page book should answer that question
once, not 200 times — and it is resolved by `PageTaskExecutor.prepare()` before page fan-out
so that an unusable engine fails the job rather than every page.

## Worked example: the fleet-wide provider budget (`T-5.7`)

The shape worth copying if you add another shared counter.

```
domain/budget.py                    the policy: fixed windows, allow/refuse, spec parsing
ports/rate_budget.py                one method: acquire(provider, cost)
adapters/persistence/rate_budget.py three implementations of it
```

Three implementations, because "shared" is a spectrum and the code should not pretend
otherwise: `UnlimitedBudget` (nothing configured), `InMemoryBudget` (one process — it
carries `fleet_wide = False` and warns at boot), and `SqlBudget` (a row per provider per
window in the job store's own database, charged under `BEGIN IMMEDIATE`).

Two decisions that are easy to get wrong:

**Windows are fixed and epoch-aligned, not sliding.** A sliding window needs a shared
notion of "the last N seconds"; a fixed window can be derived from a timestamp with no
coordination at all, which is what lets two workers agree without talking. The cost is a
possible burst across a boundary, and that is an acceptable trade for a ceiling whose job is
to avoid a wall rather than to smooth traffic.

**Exhaustion parks the work; it does not fail it.** `PageTaskExecutor` catches
`RateLimitedError`, leaves the ledger row `PENDING`, sends no callback, and the job
orchestrator holds the job's lease (`set_job_state(..., defer_seconds=...)`) until the
window resets. Reporting `failed` would be a lie — nothing was attempted — and retrying
immediately would burn attempts against a wall. The reconciler's deadline remains the
backstop if the allowance never returns.

## Job B is done — here is how it went together (`T-3.5`)

Kept as a reference, because it is the clearest example of the wrap-don't-rewrite pattern.

Three ports, one use case, and the artifact builders already existed:

```
TtsEngine   adapters/ai/tts.py            GeminiTts (paid) + SyntheticTts (free, real WAV)
Aligner     adapters/ai/aligner_whisper.py  forced transcription + speak.bind VERBATIM
RunAudioJob application/run_audio_job.py   fetch -> tokenize -> synth -> align -> upload
```

The three decisions worth copying:

**`speak.bind` was not touched.** 115 lines, pure, engine-agnostic, already proven against
two timestamp sources. Its defensive details — dropping inverted vendor intervals, a
6-token greedy match window, clamping interpolated starts so overlapping speech cannot
invert a span — are hard-won. The adapter translates dicts to `TimedToken` and nothing else.

**Transcription is injected.** `WhisperAligner(transcriber=...)` means tests run without
loading a 464 MB model while the *real* binding logic still executes. That is why the
alignment tests can cover a perfect transcription, a silent one, a partial one and a
garbage one — none of which would be reachable if the model were hard-wired.

**Duration is measured, never estimated.** `measure_wav_duration_ms` reads the WAV header.
A duration estimated from character count silently corrupts every timing in the artifact,
and there is a test asserting the shipped value equals the file's true length.

One thing that caught us: `fan_out_pages(job_id, page)` creates rows `1..page`, so a Job B
for page 7 created seven ledger rows and then confused the reconciler's audit. Job B needs
`ensure_page(job_id, page)` — exactly one row, numbered 7 — and the audit needed
generalising from a page *count* to an explicit page *set* (`audit_pages`). If you add
another single-page job type, use the same pair.

## Worked example: the Postgres store (`T-4.1`)

Most of this is already done. `SqliteJobStore` is written **in Postgres shape** — the same
table definitions, the same `ON CONFLICT DO NOTHING`, the same partial unique indexes — so
a Postgres adapter is a translation, not a design exercise.
`ai_engine/adapters/persistence/sql.py::POSTGRES_DELTA` lists the four differences, and the
only one that matters is the claim:

| Method | SQLite today | Postgres |
|---|---|---|
| `accept` | `INSERT ... ON CONFLICT (job_id) DO NOTHING` inside `BEGIN IMMEDIATE` | unchanged |
| `claim_job` / `claim_page` | `BEGIN IMMEDIATE` + `SELECT` then `UPDATE` (one writer) | `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *` — **this is what makes it multi-writer** |
| `record_artifact` | `ON CONFLICT (job_id, page_number, artifact_type) DO NOTHING` | unchanged |
| `enqueue_callback` | a partial unique index collapsing `page_completed`/`page_failed` into one slot per page | unchanged |
| Types | `TEXT` timestamps, `INTEGER` booleans, `TEXT` payloads | `timestamptz`, `boolean`, `jsonb` |

The tests are the acceptance criteria and they are already parametrized:
`tests/unit/test_job_store.py` runs all 46 contract tests against every entry in `STORES`.
Add `"postgres"` to that list and the same tests must pass unchanged —
`test_concurrent_identical_accepts_create_exactly_one_job`,
`test_two_workers_claim_different_jobs`, `test_expired_lease_is_reclaimable`,
`test_a_terminal_page_cannot_be_returned_to_pending`,
`test_a_page_gets_exactly_one_terminal_callback_slot`,
`test_a_deferred_job_is_not_claimable_until_its_deferral_expires`.

`SqlBudget` shares the same database and the same `open_connection`, so it comes along with
that translation.

DDL, indexes and the claim query are also written out in
[`../../docs/backend-v1/11-data-and-storage.md`](../../docs/backend-v1/11-data-and-storage.md).

## Worked example: inbound JWT validation (`T-2.2`)

**Blocked on `Q-01`** — but only partly. The validator can be built and unit-tested against
a fake realm today; what is unknown is the expected `azp`/`aud` value and confirmation of
the mechanism.

The seam is already there: `api/deps.py::authenticate` looks for
`request.app.state.token_verifier` and **refuses the request if `AUTH_ENFORCE=true` and no
verifier is configured** — because enforcing auth with no verifier would otherwise accept
every token.

```python
class JwksTokenVerifier:
    async def verify(self, token: str) -> dict:
        """RS256 only. iss/exp/nbf with leeway. Raise AuthenticationError otherwise."""
```

Requirements: RS256 only (symmetric algorithms are already rejected at startup — accepting
HS256 next to a JWKS verifier is the classic algorithm-confusion vulnerability); JWKS cached
with a TTL and re-fetched on an unknown `kid`, rate-limited so bogus `kid`s cannot hammer
Keycloak; `401` for "we cannot establish who you are" and `403` for "we know, and you are
not allowed"; and **no offline fallback ever** — if no key is available, every request gets
`401`.

## What not to do

| Don't | Because |
|---|---|
| Add a new HTTP endpoint for an AI stage | V1 has exactly one API surface. An internal ML function is never automatically an endpoint. |
| Import an adapter from `api/` | A test fails, and it would make minutes of blocking CPU reachable from a request handler |
| Add a third-party import to `domain/` | A test fails, and the invariant predicates stop being testable in isolation |
| Call `load_dotenv()` | A test fails, and it silently overrides injected secrets |
| Send a callback in-line instead of via the outbox | The page is lost if the process dies before the call lands |
| `break` out of a page loop on error | This is the exact bug INVARIANT-1 exists to prevent |
| Estimate `duration_ms` from character count | Measure it from the audio. The legacy code gets this right; keep it right. |
