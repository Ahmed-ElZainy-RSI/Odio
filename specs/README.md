# Specs (spec-driven development)

Layout follows GitHub Spec Kit:

```
.specify/
  memory/constitution.md                 the 10 principles every plan is checked against
  templates/                             spec / plan / tasks / checklist templates
  scripts/bash/create-new-feature.sh     scaffold specs/NNN-name/
specs/NNN-feature/                       spec.md (what/why) · plan.md (how + files) · tasks.md
docs/pipeline-vs-walidapro-report.md     the comparison behind the walidapro features
```

## Baseline: what exists today (retro-specs)

| Spec | Covers |
|---|---|
| [001-book-pipeline](001-book-pipeline/) | `src/pipeline`: PDF → tokens → tashkeel → TTS → timings → reader |
| [002-ocr-correction](002-ocr-correction/) | `tools/`: two-engine diff, VLM arbiter, human-gated context review |
| [003-ai-engine-service](003-ai-engine-service/) | `service/`: Backend V1 job contract (Job A, Job B) |
| [004-verified-tier-walidapro](004-verified-tier-walidapro/) | **epic**: port walidapro's correctness engine (index of features 005–033) |

## Features: one change each

| # | Feature | Area | Priority | Depends on | Cost / risk |
|---|---|---|---|---|---|
| 005 | [walidapro-recovery](005-walidapro-recovery/) | walidapro | **P0** | — (needs Waleed) | blocks all ports |
| 006 | [letter-preservation-guard](006-letter-preservation-guard/) | both tiers | ✅ done (CLI) | — | free |
| 007 | [reraise-programming-errors](007-reraise-programming-errors/) | service + CLI | ✅ done | — | free |
| 008 | [page-failure-isolation](008-page-failure-isolation/) | CLI pipeline | ✅ done | 007 | free |
| 009 | [page-record-schema-validation](009-page-record-schema-validation/) | CLI pipeline | ✅ done | 008 | free |
| 010 | [pymupdf-import-rename](010-pymupdf-import-rename/) | all | ✅ done | — | free |
| 011 | [dependency-lockfile](011-dependency-lockfile/) | all | ✅ done | — | free |
| 012 | [text-layer-trust-check](012-text-layer-trust-check/) | CLI + service | ◐ CLI done | — | ≤ 2 OCR calls/book |
| 013 | [content-based-book-id](013-content-based-book-id/) | CLI pipeline | ✅ done | — | free |
| 014 | [front-matter-handling](014-front-matter-handling/) | TTS | P3 | product decision | free |
| 015 | [parallel-book-runner](015-parallel-book-runner/) | CLI pipeline | ✅ done | 008, 013 | free |
| 016 | [service-diacritization](016-service-diacritization/) | service | ✅ done (opt-in) | 006 | free (CPU) |
| 017 | [vlm-third-answer-routing](017-vlm-third-answer-routing/) | tools | ✅ done | — | free |
| 018 | [correction-evidence-record](018-correction-evidence-record/) | tools + service | ✅ done | — | free |
| 019 | [correction-accuracy-harness](019-correction-accuracy-harness/) | tools | ◐ harness done, transcripts needed | reference transcripts | free (manual transcripts) |
| 020 | [page-verifier-port](020-page-verifier-port/) | service | ✅ done | 018 | free |
| 021 | [provider-call-cache](021-provider-call-cache/) | service + CLI | ✅ done (fs store) | — | saves money |
| 022 | [verified-ocr](022-verified-ocr/) | service | P1 (epic) | 005, 018, 020, 021 | 40–200 calls/page, opt-in |
| 023 | [visual-diacritics](023-visual-diacritics/) | service | P2 (epic) | 006, 020, 021, 022 | 1 call/word, opt-in |
| 024 | [final-context-review](024-final-context-review/) | service | P3 (epic) | 022 | ≤ 2 calls/page, opt-in |
| 025 | [cost-metering](025-cost-metering/) | service + CLI | ✅ done (paid-run check pending) | — | free |
| 026 | [tier-benchmark](026-tier-benchmark/) | decision | P1 (epic end) | 019, 022, 023, 025 | paid run, capped |
| 027 | [postgres-job-store](027-postgres-job-store/) | service | P2 | — | free |
| 028 | [lighton-golden-fixtures](028-lighton-golden-fixtures/) | service tests | P3 | — | ~6 calls once |
| 029 | [source-fetch-redirect-policy](029-source-fetch-redirect-policy/) | service security | ✅ done (refuse-all) | — | free |
| 030 | [contract-replay-walidapro-tests](030-contract-replay-walidapro-tests/) | service | ✅ done (Q-17/Q-18 open) | 005 | free |
| 031 | [production-config-hardening](031-production-config-hardening/) | service | ✅ done | Q-01 | free |
| 032 | [tts-chunking-dual-narrator](032-tts-chunking-dual-narrator/) | TTS | P3 | 005, product decision | 2× TTS if dual voice |
| 033 | [retire-walidapro-duplicates](033-retire-walidapro-duplicates/) | cleanup | P3 (last) | 022–024, 030, 032 | — |

## Suggested build order

```
Now, no blockers:      006 · 017 · 025 · 007 · 010 · 011 · 029 · 019 (start transcripts)
Then:                  008 → 009 · 016 (after 006) · 018 → 020 · 021 · 031 (after Q-01)
After Waleed's code:   005 → 030 · 022 → 023 → 024 · 032
Measure and decide:    026 (Q-07 → ADR-009)
Finally:               033 · 027 / 012 / 013 / 014 / 015 / 028 as capacity allows
```

## Decisions waiting on people

| Feature | Question | Who |
|---|---|---|
| 005 | Send the complete walidapro working tree | Waleed |
| 014 | Narrate title / ISBN pages? | Product |
| 023 | Visual diacritics only on already-diacritized books? | Product + tech lead |
| 024 | Should a text-only reviewer ever apply edits? (recommend no) | Tech lead |
| 026 | Q-07: is V1 accepted on protocol or on accuracy? | Product / Backend |
| 029 | Does storage ever redirect presigned URLs? | Backend |
| 031 | Q-01: token issuer and `azp` | Backend |
| 030 | Q-17: same `jobId` with a different body: `202` (spec wording) or `409` (ours)? · Q-18: `400` or `422` for a malformed request? | Backend |
| 032 | Second narrator: requirement or experiment? Voice field in Job B? | Product / Backend |

## Repo map

| Folder | Role | Specs |
|---|---|---|
| `src/pipeline/` | core pipeline library + drivers | 001, 006, 008–010, 012–015 |
| `src/tests/` | hermetic tests for 001/002 | — |
| `tools/` | OCR correction chain | 002, 017–019, 025 |
| `service/` | production AI engine | 003, 007, 016, 020–032 |
| `walidapro/` | Waleed's verification pipeline (parallel implementation) | 004, 005, 030, 033 |
| `docs/backend-v1/` | V1 analysis, ADRs, roadmap | 003, 026, 033 |
| `demo/` | benchmark / pricing / POC HTML | — |
| `tmp/` | abandoned vendor spikes | — |

New feature: `.specify/scripts/bash/create-new-feature.sh short-name` (numbers continue from 033).
