# Plan: review in context

**Spec:** [spec.md](spec.md)

```
tools/ensemble_review.py            phrase_around(); context on non-KEEP rows; backfill on re-run
tools/context_detector.py           context on each suggestion; backfill on re-run (no API call)
tools/build_context_review.py       phrase + readings + controls; "applied fixes" group; --serve
tools/apply_context_suggestions.py  accept / reject / custom on automatic fixes (undo, replace)
src/tests/test_review_context.py    phrase, backfill, page, server, undo
```

**Context** is computed from the alignment rows in `ensemble_review.json`, in order, using
`lighton_txt`: the text the detectors saw at detection time, not today's corrected text. A
Gemini-only row (no LightOn word) gets the phrase around its insertion point. The review page
fills in a missing `context` the same way, so old runs show it before any re-run.

**Decisions** stay `{id: string}` in `context_decisions.json`; only the meaning of the default
differs per kind (FR-003). Ids of the three kinds never collide: the context detector checks
KEEP rows, the image arbiter checks FLAG rows.

**Server** is stdlib `http.server`, bound to `127.0.0.1`, serving the `work/` directory (the page
references the Gemini folder's images by relative path) plus `GET|POST <book>/api/decisions`.
`POST` merges into the file (ids not on the page are kept as they are), after checking ids, value
types, size and the `Origin` header. Write is temp-file + `os.replace`.

The page detects the server by a `GET api/decisions`; when it fails (opened from disk), *Save*
becomes *Download*.
