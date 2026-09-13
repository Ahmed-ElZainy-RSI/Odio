# Reading First AI Engine — V1 Backend & AI Integration Architecture

This directory is the **implementation-ready architecture specification** for the service the AI team
(Ahmed & Waleed) must build so that the Backend team's (Rashad) book APIs can drive the existing Arabic
OCR + TTS pipeline over HTTP.

It is derived from exactly two sources:

1. **`../../assets/Backend & AI integration V1.pdf`** — the 6-page integration contract between the two
   teams. This is the only requirement document. It is a *wire protocol* spec, not a product spec.
2. **The existing repository** — 22,018 lines of Python across `src/pipeline/`, `tools/`, and
   `walidapro/`, plus 186 real page artifacts on disk under `work/`.

Every claim about the existing code in these documents carries a `path:line` citation. Every claim about
requirements is traceable to a page of the PDF.

## Epistemic labels used throughout

| Label | Meaning |
|---|---|
| **REQUIREMENT** | Stated in the V1 PDF. Non-negotiable. |
| **EXISTING BEHAVIOR** | Verified in the code, with a citation. |
| **ASSUMPTION** | We proceed on this basis; it needs confirmation. |
| **PROPOSAL** | Our design choice, not a requirement. |
| **OPEN QUESTION** | Genuinely unresolved. Listed in [22-open-questions.md](22-open-questions.md). |

Nothing in these documents presents a PROPOSAL as a REQUIREMENT. If the PDF is silent, we say so.

## Reading order

**If you have 10 minutes** → [00-executive-summary.md](00-executive-summary.md), then
[25-final-recommendation.md](25-final-recommendation.md).

**If you are the Backend team** → [02-v1-requirements.md](02-v1-requirements.md) (what we understood
your contract to be), [07-api-specification.md](07-api-specification.md) Part 1 (what we will serve) and
its "questions for the Backend team" section, then [22-open-questions.md](22-open-questions.md).

**If you are implementing** → [05-target-architecture.md](05-target-architecture.md) →
[06-service-boundaries.md](06-service-boundaries.md) →
[07-api-specification.md](07-api-specification.md) → [09-async-jobs.md](09-async-jobs.md) →
[10-data-contracts.md](10-data-contracts.md) →
[21-implementation-roadmap.md](21-implementation-roadmap.md).

**If you are reviewing the architecture** → [19-adrs.md](19-adrs.md) then
[24-architecture-challenge.md](24-architecture-challenge.md).

## Contents

### Analysis
| File | Deliverable |
|---|---|
| [00-executive-summary.md](00-executive-summary.md) | Executive architecture summary |
| [01-current-architecture.md](01-current-architecture.md) | Current architecture analysis + existing AI pipeline reconstruction |
| [02-v1-requirements.md](02-v1-requirements.md) | PDF requirements extraction (defines all `REQ-*` IDs) |
| [03-requirements-to-code-map.md](03-requirements-to-code-map.md) | Requirements → code mapping |
| [04-gap-analysis.md](04-gap-analysis.md) | Gap analysis and reuse ledger |

### Design
| File | Deliverable |
|---|---|
| [05-target-architecture.md](05-target-architecture.md) | Target architecture, option comparison, diagrams |
| [06-service-boundaries.md](06-service-boundaries.md) | Service/component boundary definitions |
| [07-api-specification.md](07-api-specification.md) | API specification (inbound + outbound) |
| [08-ai-integration.md](08-ai-integration.md) | AI integration architecture + component decision matrix |
| [09-async-jobs.md](09-async-jobs.md) | Sync vs async decisions, job lifecycle, INVARIANT-1 |
| [10-data-contracts.md](10-data-contracts.md) | Canonical data contracts and schemas |
| [11-data-and-storage.md](11-data-and-storage.md) | Data and storage architecture, DB schema |

### Cross-cutting
| File | Deliverable |
|---|---|
| [12-security.md](12-security.md) | Security architecture |
| [13-observability.md](13-observability.md) | Observability architecture |
| [14-error-model.md](14-error-model.md) | Error handling strategy |
| [15-configuration.md](15-configuration.md) | Configuration strategy |
| [16-repository-structure.md](16-repository-structure.md) | Repository structure and file dispositions |
| [17-testing-strategy.md](17-testing-strategy.md) | Testing strategy |
| [18-deployment.md](18-deployment.md) | Deployment architecture |

### Decisions and delivery
| File | Deliverable |
|---|---|
| [19-adrs.md](19-adrs.md) | Architecture Decision Records (ADR-001 … ADR-010) |
| [20-traceability-matrix.md](20-traceability-matrix.md) | Requirements traceability matrix |
| [21-implementation-roadmap.md](21-implementation-roadmap.md) | Implementation roadmap (defines all `T-*` task IDs) |
| [22-open-questions.md](22-open-questions.md) | Open questions register (`Q-*`) |
| [23-risks-and-tradeoffs.md](23-risks-and-tradeoffs.md) | Risks and trade-offs (`R-*`) |
| [24-architecture-challenge.md](24-architecture-challenge.md) | Adversarial architecture review (`C-*`) |
| [25-final-recommendation.md](25-final-recommendation.md) | Final recommended architecture |

## ID conventions

| Prefix | Defined in | Meaning |
|---|---|---|
| `REQ-AUTH/JOB/IDEM/SRC/META/UPL/CB/ART/CFG/ERR-nn` | [02-v1-requirements.md](02-v1-requirements.md) | Requirement |
| `T-<phase>.<n>` | [21-implementation-roadmap.md](21-implementation-roadmap.md) | Implementation task |
| `Q-nn` | [22-open-questions.md](22-open-questions.md) | Open question |
| `R-nn` | [23-risks-and-tradeoffs.md](23-risks-and-tradeoffs.md) | Risk |
| `C-nn` | [24-architecture-challenge.md](24-architecture-challenge.md) | Architecture challenge |
| `ADR-nnn` | [19-adrs.md](19-adrs.md) | Decision record |
| `INVARIANT-1` | [09-async-jobs.md](09-async-jobs.md) | Page Completion Totality |

## Status

**No implementation has begun.** This is deliberate: the parent instruction was to understand the system
and design the architecture before writing code. Every row of
[20-traceability-matrix.md](20-traceability-matrix.md) is therefore `NOT STARTED`.

Two things block the start of implementation, both in
[22-open-questions.md](22-open-questions.md): the inbound token validation contract (`Q-01`) and the
definition of the `Text` artifact (`Q-04`).
