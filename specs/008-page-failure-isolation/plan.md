# Plan: page failure isolation (CLI)

**Spec:** [spec.md](spec.md)

## Approach

New `src/pipeline/failures.py`:

```python
def classify_failure(exc) -> str      # "quota" | "page" | "bug"
def mark_failed(rec_path, stage, exc) # writes status=failed + error{}
```

Quota detection: HTTP 429, google-genai `ResourceExhausted` / message contains
`RESOURCE_EXHAUSTED`, Hume credit errors. Each driver's loop becomes:

```python
try: do_page(...)
except Exception as e:
    kind = classify_failure(e)
    if kind == "bug": raise
    if kind == "quota": print(...); break
    mark_failed(path, "ocr", e); failed += 1; continue
```

## Constitution check

V: this feature is principle V for the CLI pipeline. II: tokens are untouched on failure.

## Files

```
src/pipeline/failures.py                 new
src/pipeline/run_ocr_gemini.py           loop at :44-62
src/pipeline/run_ocr_lighton.py          loop at :50-61
src/pipeline/run_speak_gemini.py         loop at :78-84
src/pipeline/run_speak_hume.py           loop at :59-65
src/pipeline/run_tashkel.py              loop at :36
src/pipeline/build_reader.py             tolerate failed pages
src/tests/test_page_failure_isolation.py new
```
