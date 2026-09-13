# Testing

```
616 tests · 40s · zero network calls · zero paid API calls
  tests/unit        429   pure logic, exhaustive
  tests/api          59   the real app via TestClient
  tests/behaviour    25   the V1 protocol as spec scenarios
  tests/e2e         103   both jobs end to end, a fault matrix, crash recovery,
                          and a provider quota wall
```

Two properties are structural, not aspirational:

- **No test makes a network call.** Every external interaction — Backend, Keycloak, presigned
  storage — is served by the contract fake over an ASGI transport, or by an
  `httpx.MockTransport`.
- **No test makes a paid API call.** OCR uses the free PDF text-layer path — pinned
  explicitly as `OCR_ENGINE=textlayer` in the test settings, so no test can drift onto a
  paid provider even by accident. The paid adapters are tested against a stub client and an
  `httpx.MockTransport`, and asserted to *refuse* when no API key is configured. TTS uses a
  synthetic engine that emits genuine WAV audio; transcription is injected. The *real*
  `speak.bind` binding logic still runs in every alignment test, because that is where the
  value and the subtle bugs live.

This mirrors the repo's existing suite, where monkeypatched functions *raise* if reached —
stronger than a mock's call assertion, because an accidental network call becomes a failure
rather than a slow test.

## Test kinds, and what each is for

| Kind | Where | Question it answers |
|---|---|---|
| **Functional / unit** | `tests/unit/` | Does this function do what its contract says, including at the edges? |
| **Contract** | `tests/unit/test_job_store.py` | Does an adapter honour the port's documented guarantees? Parametrized over the in-memory *and* SQLite stores; the same 46 tests are the acceptance criteria for a Postgres adapter. |
| **Contract** | `tests/unit/test_budget.py` | Same shape for the provider budget: one contract, run against both counters, plus the fleet-wide property only the SQL one can have. |
| **Property / dynamic** | `tests/unit/test_properties.py` | Does an invariant hold for *every* input, not just the ones we thought of? |
| **API** | `tests/api/` | Does the HTTP surface behave, including status codes and the error envelope? |
| **Behavioural** | `tests/behaviour/` | Did we understand the V1 spec? Each test quotes the sentence it enforces. |
| **End to end** | `tests/e2e/` | Does the whole protocol work, in order, against something that checks us back? |
| **Fault injection** | `tests/e2e/test_fault_matrix.py` | Does INVARIANT-1 survive every way the world can break? |
| **Concurrency / stress** | `tests/e2e/test_fault_matrix.py` | Do six concurrent books all complete, share one token, and never double-report? |
| **Durability / crash** | `tests/e2e/test_durability.py` | Does a worker killed mid-page leave the book recoverable, with no page reported twice? |
| **Quota exhaustion** | `tests/e2e/test_provider_budget.py` | When a free tier runs out mid-book, is the rest parked rather than falsely failed — and does the book finish when the window resets? |

## The contract fake

`tests/fakes/backend.py` — the highest-leverage piece of test infrastructure here. The whole
V1 risk is protocol conformance, and without this nothing could be verified until the
Backend team was available.

It implements Keycloak's token endpoint, presigned source download (per-`fileId`, so one
fake serves a PDF for Job A and a text file for Job B), the three-step upload cycle, and all
four progress callbacks. It does three things a mock cannot:

It is also runnable by hand: [`../devtools/fake_backend_server.py`](../devtools/fake_backend_server.py)
serves this same app over a real socket, with `_`-prefixed routes for injecting faults and
inspecting what it received. Same fake, so what you poke at by hand is what the tests
assert against. See
[04-local-development.md](04-local-development.md#run-the-whole-thing-locally).

**1. Records call order.** Four of the mandated rules are ordering rules — metadata before
page work, image before text, both files confirmed before `completed`, and the three upload
steps in sequence. Those are only testable against something that remembers.

**2. Injects 16 faults on demand.**

```python
fake.faults.source_expired_times = 1  # presigned 403, then success
fake.faults.source_missing = {"book-1"}  # permanent 404
fake.faults.initialize_upload_status = 503
fake.faults.initialize_upload_failures = 2
fake.faults.put_status = 429
fake.faults.put_failures = 1
fake.faults.retry_after = "0"
fake.faults.confirm_status = 401  # with confirm_failures=1: 401 once, then OK
fake.faults.callback_status = 503
fake.faults.token_status = 401
```

**3. Enforces the contract back at us.** It returns errors for *our* mistakes, so they
surface here rather than in a joint debugging session:

| The fake rejects | Status |
|---|---|
| A content type outside the four permitted ones | 422 |
| `Content-Length` disagreeing with the body | 400 |
| `Content-Type` not matching the ticket | 400 |
| An `Authorization` header on a presigned PUT (which would leak our token to storage) | 400 |
| `confirm-upload` before the PUT | 409 |
| `completed` naming a file that was never confirmed | 409 |
| A missing bearer token on any Backend call | 401 |
| An empty `reason` | 422 |

That last group matters: several e2e tests assert little more than "the job completed", and
they are meaningful *because* the fake would have rejected a malformed request.

Its `terminal_reports()` helper **raises** if any page is reported twice, so every test that
calls it is also a duplicate-report test.

## The fault matrix

`tests/e2e/test_fault_matrix.py` does not assert a specific outcome per fault. It asserts
the one property that must hold no matter what breaks:

> For every expected page there is a terminal outcome that is either **delivered** to the
> Backend or **durably queued** for delivery. Nothing is silently dropped, and nothing is
> reported twice.

That phrasing is deliberate and was arrived at the hard way. An earlier version asserted
"every page reported, or the job failed", and it failed for the four faults where the
*Backend itself* is unreachable — `callback_flaky`, `callback_rejects`, `token_down`,
`token_rejects`. In those cases we genuinely cannot tell the Backend anything; the callbacks
sit durably in the outbox waiting. That is not a bug, it is the only possible behaviour, and
the design says so. The invariant had to be stated in terms of what we can actually
guarantee.

16 faults × 2 job types, plus every combination of failing pages.

## Property-based tests

`tests/unit/test_properties.py` (hypothesis, 200 examples each) covers invariants that
example tests can only sample:

| Property | Why it matters |
|---|---|
| `tokenize` → `build_text` → `tokenize` is the identity | **The whole Job A → Job B contract.** If this fails, the two jobs disagree about token ids and the reader cannot line up timings with text. |
| Token ids are always contiguous and page-prefixed | Downstream references (boxes, timings) are keyed on them |
| Tokenizing is insensitive to whitespace shape | Newlines and runs of spaces must not shift any id |
| No token ever contains whitespace | This is what makes the `Text` artifact losslessly invertible |
| Monotonic input always yields a valid timing document | The four guarantees a reader relies on |
| `t_src` present exactly when `t` is | There is no third state to interpret |
| `anchor_ratio` is always a probability; `BindReport` counts always add up | |
| A `reason` is always ≤512 chars, code-prefixed, and credential-free | The Backend stores and displays these |
| Redaction is idempotent | Redacting twice must not corrupt an already-safe string |
| `payload_hash` is deterministic, and changes with any material field | Otherwise a different job is accepted as a duplicate |
| `is_complete` ⟺ all expected pages terminal, over arbitrary ledgers | The definition of INVARIANT-1 |

## Retry coverage

`tests/unit/test_retry.py` — 66 tests, all deterministic via an injected clock, sleep and
RNG, so nothing actually waits.

- The decision table is a **pure function** (`classify`) and is tested directly against
  every retryable and every terminal status code.
- Full jitter is checked for bounds, for actual variation, and for distribution.
- `Retry-After` is tested in both RFC 7231 forms (delay-seconds and HTTP-date), including a
  past date clamping to zero and a hostile 3600 being capped by `max_delay`.
- The 401 rule: refresh once, then terminal — and never refresh twice for a persistent 401.
- A wall-clock budget stops the loop even when the attempt cap has not been reached.
- No sleep after the final attempt (that would be pure dead time).

## Auth coverage

`tests/unit/test_auth_jwks.py` (35) generates **real RSA keys** and runs a fake Keycloak
realm. Mocking `jwt.decode` would pass every one of these:

- HS256 and `alg: none` rejected — the algorithm-confusion class, where a public JWKS key
  doubles as a valid HMAC secret.
- A token signed by a foreign key rejected.
- Expiry, not-yet-valid, missing `exp`, foreign issuer, wrong audience.
- Key rotation: an unknown `kid` triggers one refresh and then succeeds.
- Refreshes are rate-limited so bogus `kid`s cannot hammer the realm — **but a genuine
  rotation is never blocked.** An earlier version rate-limited all refreshes and would have
  caused a 60-second auth outage after every key rotation; the limit now applies only to
  refreshes that returned the same key set.
- Every failure mode fails **closed**: unreachable realm, 5xx, malformed document, empty key
  set. A verifier that fails open is worse than none, because it looks like protection.

`tests/api/test_auth_enforced.py` (17) runs the same scenarios through the real endpoint,
and adds the configuration mistake most likely to happen in a hurry: `AUTH_ENFORCE=true`
with no verifier wired must reject everything.

## Structural tests

`tests/unit/test_domain_purity.py` asserts three architectural claims by parsing the AST, so
they cannot rot into comments:

| Test | Claim |
|---|---|
| `test_domain_imports_only_stdlib_and_itself` | `domain/` needs no third-party package |
| `test_api_layer_never_imports_adapters_or_legacy_pipelines` | No AI code path is reachable from an HTTP handler |
| `test_no_load_dotenv_anywhere_in_the_service_package` | Injected secrets cannot be silently overridden |

The third checks the AST for real imports and calls rather than grepping for the string, so
documentation that *mentions* the anti-pattern does not trip it — a lesson learned when the
first version flagged its own docstring.

## Bugs these suites caught

Worth recording, because each was found by a test rather than in production:

| Bug | Found by | Why it mattered |
|---|---|---|
| **The metadata gate was not a gate** — callbacks were enqueued and page work proceeded immediately | `test_metadata_is_sent_before_any_upload` | Uploads raced ahead of the page count the Backend needs first |
| **Key-rotation auth outage** — the JWKS refresh rate limit blocked legitimate rotations for 60 s | `test_an_unknown_kid_triggers_one_refresh_and_then_succeeds` | Every realm key rotation would have caused a rejection window |
| **Logging died after any stdout swap** — `PrintLoggerFactory(file=sys.stdout)` captures the stream at configure time | Cross-file test pollution | Any supervisor or log-rotation wrapper replacing `sys.stdout` would break logging permanently |
| **A store outage returned 500, not 503** | Verifying a test cited in the docs actually existed | A 500 would make the Backend not retry, stranding the book |
| **Job B created ledger rows 1..N instead of one** | `test_job_b_completes_and_reports_the_page` | A Job B for page 7 created 7 rows and confused the reconciler's audit |
| **`pydantic-settings` JSON-decodes list env vars** before validators run | Running the server for real | `SOURCE_URL_ALLOWED_HOSTS=host` crashed at boot while every unit test passed |
| **Over-redaction ate URL routes** — `"path": "[REDACTED]"` | Reading real log output | A log line that cannot say which endpoint failed is close to worthless |
| **A page could be reported twice, contradicting itself** — `failed` then `completed` | `test_a_worker_that_dies_mid_book_is_resumed_by_the_next_one` | Two real bugs at once: the store let a terminal page reset to `PENDING`, and outbox uniqueness was per-*kind*, so both reports fit. Worse than a duplicate: the Backend would act on the wrong one. |
| **A resumed job re-ran every page** | Surfaced while fixing the above | Every page of a resumed book would repeat its paid AI work, and its second callback would be refused |
| **The API served an in-memory store in production** — `run_api` never opened the configured one | `test_the_api_serves_the_configured_store_when_none_is_injected` | Jobs returned `202` and then never happened: no worker could see them |
| **The SSRF resolve-then-reject check was disabled in production** — the composition root hardcoded `resolve_and_check=False` with a comment about ASGI transports | Reading `worker.py` while wiring the local stack | The control was correct and fully unit-tested; it was simply never switched on in the composed worker. An allowlisted hostname resolving to `169.254.169.254` would have been fetched. Now a setting, secure by default, asserted **through the composition root**. |

The recurring lesson: a test that constructs its subject directly cannot see a boundary
problem. Keep the e2e and behavioural suites honest.

## Running

```bash
python -m pytest -q                    # 616 passed, ~40s
python -m pytest -q -m "not slow"      # 607 passed, ~19s — the fast loop
python -m pytest tests/unit -q         # 429 tests, ~6s
python -m pytest tests/behaviour -q    # the protocol, as prose
python -m pytest tests/e2e/test_fault_matrix.py -q   # 42 fault cases, ~3s
python -m pytest tests/e2e/test_provider_budget.py -q  # the quota wall
```

CI runs exactly this plus the legacy suite —
[`../../.github/workflows/service-ci.yml`](../../.github/workflows/service-ci.yml):

```bash
ruff check . && ruff format --check .
python -m pytest -q                    # service
python -m pytest ../src/tests -q       # the 56 pre-existing legacy tests, from the repo root
```

`src/tests` is in the gate because this service calls into `src/pipeline` directly
(`adapters/ai/legacy.py`): breaking those tests breaks this service, and the failure would
otherwise only appear in production.

`-m "not slow"` skips the suites that render a 16-page book at 2×.

## Adding a test

| Kind | Where | Rules |
|---|---|---|
| Pure logic | `tests/unit/` | No I/O. Milliseconds. |
| An invariant | `tests/unit/test_properties.py` | State it as a property over generated input |
| Adapter contract | `tests/unit/test_job_store.py` | Assert the *port's* documented behaviour, not the implementation's internals |
| HTTP contract | `tests/api/` | Use the `client` fixture. Assert status codes and the error envelope. |
| Spec conformance | `tests/behaviour/` | Quote the sentence you are enforcing in the docstring |
| Protocol | `tests/e2e/` | Use `FakeBackend`. Assert **call order**, not just outcomes. |

Async tests need no decorator — `asyncio_mode = "auto"`. Mark anything that renders many
real pages `@pytest.mark.slow`. Prefer `small_pdf_bytes(4)` over the 16-page corpus book
unless the test is specifically about scale, and set the fast retry knobs in test settings
so backoff does not dominate runtime.
