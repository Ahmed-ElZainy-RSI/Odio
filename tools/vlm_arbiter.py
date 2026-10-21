"""Offline step 2: ask Gemini vision to arbitrate the disagreements
tools/ensemble_review.py already found.

Reads ensemble_review.json (never touches pXXX.json), and for every page
that has at least one FLAG, sends the already-rendered page image
(<gemini_dir>/pages/pXXX.jpg -- saved by run_ocr_gemini.py regardless of
per-token box) plus the list of disagreeing ids/candidates in one request.
One call per flagged PAGE, not per flagged token: cheaper, and it needs no
bounding box on either side -- LightOn's missing geometry (see
tmp/lighton_bbox_support_request.md) stops mattering entirely, including
for insert/delete cases that had no box on either engine.

Writes verified_txt back into the same report, in place. Still does not
touch pXXX.json or reader.html -- applying an accepted verdict into the
pipeline's own token records is a separate, later step.

Usage:
    python3 tools/vlm_arbiter.py "work/<book>-lighton/ensemble_review.json"
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pipeline import usage  # noqa: E402
from pipeline.ocr_gemini import MODEL  # noqa: E402

SCHEMA = types.Schema(
    type="ARRAY",
    items=types.Schema(
        type="OBJECT",
        properties={
            "id": types.Schema(type="STRING"),
            "text": types.Schema(type="STRING"),
        },
        required=["id", "text"],
    ),
)

PROMPT = (
    "Two OCR engines transcribed this page and disagreed on the words listed "
    "below. For each id, look at the image and write EXACTLY what is "
    "printed there -- diacritics included, verbatim. Do not just repeat "
    "either candidate if both are wrong; write what you actually see. "
    "One entry per id, in the same order.\n\n"
    "Disagreements:\n{items}"
)


def arbitrate_page(client, jpeg: bytes, flagged: list[dict], retries=4) -> dict[str, str]:
    """flagged: [{id, lighton_txt, gemini_txt}, ...] for one page.
    Returns {id: verified_text}."""
    lines = [f"- id {f['id']}: engine A says {f['lighton_txt']!r}, "
             f"engine B says {f['gemini_txt']!r}" for f in flagged]
    prompt = PROMPT.format(items="\n".join(lines))

    last = None
    for attempt in range(retries):
        try:
            r = client.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=SCHEMA,
                ),
            )
            usage.record("gemini", MODEL, "vlm-arbiter", images=1, **usage.gemini_units(r))
            out = json.loads(r.text)
            return {row["id"]: row["text"] for row in out}
        except Exception as e:
            last = e
            time.sleep(2 ** attempt * 2)
    raise last


def _run(review_path: Path) -> Path:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    gemini_dir = Path(review["gemini_dir"])
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    arbitrated = 0
    for page_no, results in review["pages"].items():
        flagged = [r for r in results if r["action"] == "FLAG"]
        if not flagged:
            continue
        jpeg_path = gemini_dir / "pages" / f"p{int(page_no):03d}.jpg"
        if not jpeg_path.exists():
            print(f"page {page_no}: no rendered image at {jpeg_path}, skipping")
            continue

        try:
            with usage.page(int(page_no)):
                verdicts = arbitrate_page(client, jpeg_path.read_bytes(), flagged)
        except Exception as e:
            # write what's already been arbitrated before stopping -- a
            # crash (quota, network) used to lose every page done so far
            # since the write only happened once, at the very end
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"page {page_no}  FAILED: {type(e).__name__}: {str(e)[:150]}")
            print(f"{arbitrated} token(s) from earlier pages saved -> {review_path}")
            print("stopping -- rerun to resume from here")
            return review_path

        for r in flagged:
            if r["id"] in verdicts:
                r["verified_txt"] = verdicts[r["id"]]
                r["action"] = "VERIFIED"
                r["vlm_model"] = MODEL  # provenance for the evidence record (spec 018)
                arbitrated += 1
        print(f"page {page_no}: {len(verdicts)}/{len(flagged)} arbitrated")
        # persisted every page, not just at the end, for the same reason
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{arbitrated} tokens arbitrated -> {review_path}")
    return review_path


def run(review_path: Path) -> Path:
    """Arbitrate flagged pages, recording every paid call to usage.jsonl (spec 025)."""
    with usage.recording(review_path.parent, "vlm-arbiter"):
        return _run(review_path)


def main():
    ap = argparse.ArgumentParser(
        description="Arbitrate ensemble_review.json's FLAGged tokens with Gemini vision.")
    ap.add_argument("review_json", help="path to ensemble_review.json")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    run(review_path)


if __name__ == "__main__":
    main()
