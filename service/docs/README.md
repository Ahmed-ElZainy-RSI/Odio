# Implementation documentation

How the AI Engine service is built, run and extended. For *why* it is built this way — the
requirements extraction, the architecture options, the decision records — see
[`../../docs/backend-v1/`](../../docs/backend-v1/).

| Document | Read it when |
|---|---|
| [01-architecture.md](01-architecture.md) | You want the module map and the rules that hold it together |
| [02-api-reference.md](02-api-reference.md) | You are calling this service, or changing what it serves |
| [03-configuration.md](03-configuration.md) | You are deploying it or wondering what a setting does |
| [04-local-development.md](04-local-development.md) | You want to run a book through it on your machine |
| [05-testing.md](05-testing.md) | You are adding a test, or a test is failing |
| [06-extending.md](06-extending.md) | **You are implementing the next piece.** Start here. |
| [07-status-and-roadmap.md](07-status-and-roadmap.md) | You want to know what is done and what is next |
| [08-operations.md](08-operations.md) | Something is wrong in a deployed environment |

## The short version

The service accepts a job over HTTP, returns `202` immediately, and does the work in a
worker.

**Job A** downloads a PDF, reports its page count, then for each page renders an image,
extracts the text, uploads both through the Backend's three-step upload cycle, and reports
the page — exactly once, whatever happens.

**Job B** downloads one page's text, reproduces Job A's token ids from it, synthesizes
audio, recovers word timings by forced alignment, and uploads the audio plus a timing JSON.

The AI itself is not reimplemented. It is reached through ports, and the adapters wrap the
existing `src/pipeline` code — `speak.bind` is reused verbatim.

Everything durable — the job ledger, the callback outbox, the provider budget — lives in
one SQLite database in Postgres shape, so the API, the workers and the reconciler coordinate
through a single file (or, later, a single Postgres).

This package is self-contained. It does not touch `walidapro/api/`.

## Conventions used throughout

| Label | Meaning |
|---|---|
| **REQ-\*** | A requirement from the V1 PDF. Defined in [`../../docs/backend-v1/02-v1-requirements.md`](../../docs/backend-v1/02-v1-requirements.md). |
| **T-\*** | An implementation task. Defined in [`../../docs/backend-v1/21-implementation-roadmap.md`](../../docs/backend-v1/21-implementation-roadmap.md). |
| **Q-\*** | An open question. Defined in [`../../docs/backend-v1/22-open-questions.md`](../../docs/backend-v1/22-open-questions.md). |
| **INVARIANT-1** | Page Completion Totality. Every page reports exactly once, or the job fails. |
