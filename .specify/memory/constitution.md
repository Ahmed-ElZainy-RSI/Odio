# ocr_review_models — Constitution

Scanned Arabic children's books → an interactive read-along library (page image + reflowed
text + narrated audio, highlighted word by word). Design point: N ≥ 1,000 books.

These principles govern every spec, plan and task under `specs/`. A plan that violates one
must say so in its *Complexity / Constitution check* section and justify it.

**Version:** 1.0.0 · **Ratified:** 2026-10-20 · **Last amended:** 2026-10-20

---

## I. Correct before you diacritize

An OCR error that is a *valid Arabic word* (`الفضل` for `الفصل`) passes every spellchecker
and is then diacritized and spoken fluently and wrongly. Letter correction MUST run before
diacritization, and diacritization MUST NOT run on text that has not passed the
correctness gate for its tier.

## II. Stable token anchoring

Every word gets an id at extraction time — `p{page:03d}w{index:03d}` — and it is never
renumbered. Correction, diacritization, chunking, TTS timings, bounding boxes and review
rows attach to the id, never to string offsets. Any stage that cannot preserve the id ↔
word mapping (e.g. a model returns a different word count) MUST leave the page untouched and
report it, not realign heuristically.

## III. Uncertain ⇒ keep the original and flag

No stage may change an Arabic letter without evidence that is independent of the
proposal being checked. Specifically:

- Context alone never authorizes a letter change.
- A vision model asked to verify a word reads the crop **blind** (never sees the original,
  the candidate or the sentence).
- `UNCLEAR` is zero evidence, not weak evidence.
- Corrections are applied to a verified, unique character span — never `str.replace`.
- Text-only "final review" passes are opt-in and refuse rather than guess.

## IV. Every stage proves it stayed in its lane

Each stage ships a mechanical check that it did not exceed its mandate, written *before*
its output is trusted: diacritization compares letters with marks stripped; TTS checks
duration against word count; the binder reports anchored / interpolated / untimed counts.
A failed check fails the page, not the book.

## V. Per-page isolation and resumability

The unit of failure is the page. One page's exception MUST NOT abort the remaining pages,
and MUST produce exactly one terminal report (completed or failed). Every stage is
resumable: a resume check recognises **every later state**, not just the next one, and a
quota stall costs at most the page in flight.

## VI. Prefer the free, exact path

If a PDF page carries a trustworthy text layer, use it (exact text, per-word boxes, zero
paid calls). OCR is for pages that need it. `--force-ocr` exists because "has a text
layer" ≠ "the text layer is right".

## VII. Cost is a design input

Paid calls per page are a first-class metric. A feature that multiplies them (e.g. one
vision call per word) MUST state the per-page and per-1,000-book cost, be behind a flag
that defaults off, and be metered by the provider budget.

## VIII. Test without paying

Every test runs hermetically: zero network, zero paid API calls. External providers are
behind ports with fakes. New code without tests does not become a default path.

## IX. Layering

`domain/` is stdlib-only. The API layer never imports adapters, SDKs or legacy pipelines.
Only adapters may import provider SDKs or `src/pipeline` / `walidapro`. Enforced by tests.

## X. Honest reporting

Docs and status tables state what was measured, on what, and what is a projection. Known
gaps are written down where the next reader will look, not discovered later.

---

## Governance

- Amend by PR that edits this file, bumps the version (MAJOR: principle removed/redefined;
  MINOR: principle added; PATCH: wording), and updates affected plans.
- Every `plan.md` contains a *Constitution check* table against I–X.
