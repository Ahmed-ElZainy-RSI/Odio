"""Offline layer: catch correlated errors -- LightOn and Gemini agreeing on
the SAME wrong word, which tools/ensemble_review.py can never see (its
detection is purely "do the two engines disagree"; agreement is marked
KEEP and nothing ever looks at it again).

Runs on every page's KEEP entries -- not just already-flagged pages, since
the whole point is finding errors that produced no disagreement at all.
One Gemini text-only call per page (same per-page batching vlm_arbiter.py
uses), asking whether any agreed-upon word looks linguistically wrong in
context.

This is Approach 2's "Context Model" from docs/ocr-correction-approaches-en.md
Section 4.2 -- and its hard rule applies here too, even more so: a context
model has no image to check itself against, so its guess can be a
confidently wrong, linguistically-plausible word (the exact
"العنصر"/"العنبر" example that opens that doc). So this tool ONLY suggests;
it never writes to pXXX.json and never touches ensemble_review.json (kept
completely separate on purpose -- see this tool's own docstring history:
ensemble_review.json already nearly lost real vlm_arbiter.py work to a
careless write once tonight). A human reviews context_review.json by hand
and decides accept/reject/edit; tools/apply_context_suggestions.py applies
only what was explicitly accepted.

Engine: OpenRouter (deepseek/deepseek-v4-pro-0813) rather than Gemini --
deliberately a different provider/quota pool than every vision-dependent
tool in this pipeline (ocr_gemini.py, vlm_arbiter.py). This step is
text-only, so it doesn't need Gemini's vision capability at all, and
keeping it off Gemini means a day where that quota is exhausted (a real,
repeated problem tonight) doesn't also block this layer.

Usage:
    python3 tools/context_detector.py "work/<book>-lighton/ensemble_review.json"
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
load_dotenv()

MODEL = "deepseek/deepseek-v4-pro-0813"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

PROMPT = (
    "Two independent OCR engines both read this Arabic page and AGREED on "
    "every word below -- but agreement doesn't mean correct; both could "
    "have made the same misreading. Read the page as continuous text and "
    "flag ONLY words that look linguistically wrong in context (an odd "
    "word choice, a broken grammatical agreement, a term that doesn't fit "
    "the surrounding sentence). For each one, give your suspicion as a "
    "0.0-1.0 score and your best guess at the correct word. Do not flag "
    "words that are merely rare or classical -- only ones that seem like "
    "a genuine misreading.\n\n"
    "Page text (id: word):\n{items}\n\n"
    'Respond with ONLY a JSON object: {{"flagged": [{{"id": "...", '
    '"suspicion": 0.0, "proposed": "..."}}, ...]}}. Use an empty array if '
    "nothing looks wrong."
)

JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def detect_page(api_key: str, keep_entries: list[dict], retries=4) -> list[dict]:
    lines = [f"{r['id']}: {r['lighton_txt']}" for r in keep_entries]
    prompt = PROMPT.format(items="\n".join(lines))

    last = None
    for attempt in range(retries):
        try:
            r = requests.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=90,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            content = r.json()["choices"][0]["message"]["content"]
            m = JSON_BLOCK.search(content)
            parsed = json.loads(m.group(0) if m else content)
            return parsed.get("flagged", [])
        except Exception as e:
            last = e
            time.sleep(2 ** attempt * 2)
    raise last


def run(review_path: Path, out_path: Path | None = None) -> Path:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    lighton_dir = review_path.parent
    out_path = out_path or (lighton_dir / "context_review.json")

    # resumable: skip pages already present, same reason ensemble_review.py
    # and vlm_arbiter.py had to learn tonight -- a crash mid-run must not
    # force re-spending API calls on pages already done
    if out_path.exists():
        report = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        report = {"lighton_dir": str(lighton_dir), "pages": {}}

    api_key = os.environ["OPENROUTER_API_KEY"]
    suspicious_total = 0
    for page_no, results in review["pages"].items():
        if page_no in report["pages"]:
            continue
        keep_entries = [r for r in results if r["action"] == "KEEP" and r["lighton_txt"]]
        if not keep_entries:
            report["pages"][page_no] = []
            continue

        try:
            flagged = detect_page(api_key, keep_entries)
        except Exception as e:
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"page {page_no}  FAILED: {type(e).__name__}: {str(e)[:150]}")
            print(f"{suspicious_total} suspicious word(s) from earlier pages saved -> {out_path}")
            print("stopping -- rerun to resume from here")
            return out_path

        by_id = {r["id"]: r["lighton_txt"] for r in keep_entries}
        entries = [{"id": f["id"], "ocr_word": by_id.get(f["id"], "?"),
                    "suspicion": f["suspicion"], "proposed": f["proposed"]}
                   for f in flagged if f["id"] in by_id]
        report["pages"][page_no] = entries
        suspicious_total += len(entries)
        print(f"page {page_no}: {len(keep_entries)} agreed words checked, {len(entries)} suspicious")
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{suspicious_total} suspicious word(s) total -> {out_path}")

    # decisions template: defaults every new suspicion to "reject" (safe --
    # nothing applies unless a human changes it) and never touches an id a
    # reviewer has already decided on, so re-running the detector after
    # adding more pages doesn't wipe out review work already done
    decisions_path = lighton_dir / "context_decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.exists() else {}
    added = 0
    for entries in report["pages"].values():
        for e in entries:
            if e["id"] not in decisions:
                decisions[e["id"]] = "reject"
                added += 1
    if added:
        decisions_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{added} new entry/entries added to {decisions_path} (default \"reject\")")
    print(f"\nreview {out_path} for context (ocr_word/proposed/suspicion), then edit "
          f"{decisions_path}: set a value to \"accept\" to take the proposed word, or to "
          f"any other string to use that instead. Nothing applies until "
          f"tools/apply_context_suggestions.py runs against your edits.")
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Flag agreed-upon (KEEP) words that may still be a correlated OCR error.")
    ap.add_argument("review_json", help="path to ensemble_review.json")
    ap.add_argument("--out", default=None, help="output path (default: <lighton_dir>/context_review.json)")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    run(review_path, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
