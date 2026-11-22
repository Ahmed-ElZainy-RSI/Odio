"""Page OCR through the Gemini Batch API: the same request as ocr_gemini.ocr_page(), half price.

A batch job takes every page of a folder at once and returns all the answers together. Google's
target turnaround is 24 hours ("in majority of cases, it is much quicker"); a job still pending or
running after 48 hours expires and returns nothing. Prices: config/prices.toml, model
"gemini-3.8-flash:batch".

The flow, driven by tools/run_folder.py --batch / --collect:

    lines = [request_line(key, jpeg) ...]           # one per page, same prompt/schema/settings
    job   = submit(client, lines, "دار نهضة مصر")    # uploads a JSONL file, creates the job
    job   = client.batches.get(name=job.name)       # later: QUEUED / PENDING / RUNNING / ...
    for key, lines, units, error in results(client, job):
        ...                                         # model lines -> run_ocr_gemini.finish_page()

Every page image is sent to Google, exactly as in the page-by-page run.
"""
import base64
import json
import tempfile
from pathlib import Path

from google.genai import types

from pipeline.ocr_gemini import MODEL, PROMPT, SCHEMA

PRICE_MODEL = f"{MODEL}:batch"   # how batch calls are recorded in usage.jsonl and priced
FINISHED = {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED", "JOB_STATE_FAILED",
            "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
HAS_RESULTS = {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"}


def request_line(key: str, jpeg: bytes) -> dict:
    """One JSONL line: the page image + the same prompt, schema and temperature as ocr_page()."""
    return {
        "key": key,
        "request": {
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")}},
                {"text": PROMPT},
            ]}],
            "generation_config": {
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_schema": SCHEMA.model_dump(mode="json", exclude_none=True),
            },
        },
    }


def submit(client, lines: list[dict], display_name: str):
    """Upload the requests as a JSONL file and create the batch job. Returns the BatchJob."""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        path = Path(f.name)
    try:
        uploaded = client.files.upload(file=str(path), config=types.UploadFileConfig(
            display_name=f"ocr-{len(lines)}-pages", mime_type="jsonl"))
    finally:
        path.unlink(missing_ok=True)
    # display_name is shown in Google's console; keep it ASCII-safe and short
    safe = display_name.encode("ascii", "ignore").decode() or "ocr-batch"
    return client.batches.create(model=MODEL, src=uploaded.name,
                                 config=types.CreateBatchJobConfig(display_name=safe[:100]))


def state(job) -> str:
    s = job.state
    return getattr(s, "name", None) or str(s)


def _units(meta: dict) -> dict:
    """Billed tokens from a batch response's usage metadata (camelCase or snake_case)."""
    g = lambda *k: next((meta[x] for x in k if meta.get(x) is not None), 0)
    return {
        "input_tokens": int(g("promptTokenCount", "prompt_token_count")),
        # thinking tokens bill as output, as in usage.gemini_units()
        "output_tokens": int(g("candidatesTokenCount", "candidates_token_count"))
                         + int(g("thoughtsTokenCount", "thoughts_token_count")),
    }


def results(client, job):
    """Yield (key, lines | None, units, error | None) for every request in a finished job.

    lines is the parsed [{text, box_2d}] list, as ocr_page() returns. A request Google failed, or an
    answer that isn't valid JSON, gives lines=None and the reason in error (its tokens, if any were
    billed, are still in units).
    """
    if state(job) not in HAS_RESULTS or not job.dest or not job.dest.file_name:
        return
    raw = client.files.download(file=job.dest.file_name).decode("utf-8")
    for row in raw.splitlines():
        if not row.strip():
            continue
        item = json.loads(row)
        key = item.get("key")
        if item.get("error"):
            yield key, None, {}, json.dumps(item["error"], ensure_ascii=False)[:300]
            continue
        resp = item.get("response") or {}
        units = _units(resp.get("usageMetadata") or resp.get("usage_metadata") or {})
        try:
            parts = resp["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            yield key, json.loads(text), units, None
        except (KeyError, IndexError, TypeError, ValueError) as e:
            yield key, None, units, f"unreadable answer: {type(e).__name__}: {e}"[:300]
