# Research: Verified Tier

Full comparison: [`docs/pipeline-vs-walidapro-report.md`](../../docs/pipeline-vs-walidapro-report.md).

## Decisions

| # | Decision | Rationale | Alternatives rejected |
|---|---|---|---|
| R1 | Port walidapro's **engine** (`ocr_verifier/`), not its API or orchestrator | `service/` already implements the V1 contract completely and with tests; walidapro's API is missing its worker module | Merging both APIs (two idempotency models, two stores); adopting walidapro's API (in-process threads, no outbox/reconciler) |
| R2 | Integrate through the existing `PageVerifier` port | ADR-009 made enabling the tier a config change, not an architecture change | Calling walidapro from `src/pipeline` drivers |
| R3 | Re-key corrections from string spans to **token ids** | Timings, boxes and the reader all hang off ids (constitution II) | Keeping span-based application and re-tokenizing afterwards (breaks anchoring when length changes, e.g. `مريي`→`مرئي`) |
| R4 | Port `_check_preservation` to **both** tiers first | Cheap, no API cost, and fixes a real gap in our `tashkel.py` | Leaving it verified-tier only |
| R5 | Keep our local diacritizer for the fast tier; visual per-word diacritics only in verified | 35–137 vision calls per page vs free | Visual everywhere |
| R6 | Keep `tools/` human gate alongside the verifier | It catches errors both engines agree on; walidapro's ContextReviewer + blind read is the automated analogue, but the human gate is the only defence for proper names | Replacing tools/ outright |
| R7 | PaddleOCR in a separate worker image target | Weights + paddlepaddle make the default image heavy | Bundling into the base image |

## Open questions

- Q-07 (acceptance on protocol vs accuracy): decides whether this ships in V1 at all.
- Who owns walidapro going forward? Bilingual comments and vertical style differ from `service/` conventions. Pick one style for ported code (proposal: `service/` ruff style, keep the Arabic reasoning comments).
- Dual narrator: is a male voice a product requirement, or was it an experiment?
