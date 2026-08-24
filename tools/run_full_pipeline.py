"""One command for the whole chain documented in docs/full-pipeline-guide.html:
both OCR engines -> detect -> arbitrate -> apply corrections -> re-synthesize
-> catch correlated errors -> build the review page -- then it STOPS.

It stops there on purpose. tools/apply_context_suggestions.py is the one
step in this entire pipeline that requires a human decision (see
context_detector.py's own docstring: a text-only guess has no image to
check itself against, so nothing from that stage is ever auto-applied).
A "run everything" command that skipped that would be re-introducing
exactly the linguistic-guessing risk this whole project was built to
avoid -- so this script does not offer a flag to bypass it.

Every stage it calls is independently resumable (each tool checks what's
already done before spending an API call again), so if a daily quota cuts
a run short partway through -- expected, not a bug, see the guide's
"Known constraints" table -- just run this same command again later. It
picks up wherever it stopped, at zero wasted cost for whatever finished.

Needs the vx2lm conda env (fitz, google-genai, faster-whisper):
    /Users/ahmedmostafa/miniconda3/envs/vx2lm/bin/python3 \\
        tools/run_full_pipeline.py "assets/PDF Samples/<book>.pdf"
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import apply_corrections
import build_context_review
import context_detector
import ensemble_review
import replace_page
import vlm_arbiter
from pipeline import build_reader, main as main_pipeline, run_speak_gemini, run_tashkel

REPLACE_THRESHOLD = 0.5  # matches replace_page.py's own default


def step(n: str, title: str):
    print(f"\n{'='*70}\n[{n}] {title}\n{'='*70}")


def run(pdf_path: Path) -> Path:
    book = pdf_path.stem.strip()
    gemini_dir = Path(f"work/{book}")
    lighton_dir = Path(f"work/{book}-lighton")

    step("1/8", f"Gemini OCR pass -> {gemini_dir}")
    main_pipeline.run_pipeline(pdf_path, ocr="gemini", tts="gemini")

    step("2/8", f"LightOn OCR pass -> {lighton_dir}")
    if not lighton_dir.exists():
        # main.py always writes to work/<book> with no engine suffix -- move
        # the just-finished Gemini result aside so the LightOn run doesn't
        # collide with it, then rename LightOn's result into place
        tmp = Path(f"work/{book}-gemini-tmp")
        shutil.move(str(gemini_dir), str(tmp))
        main_pipeline.run_pipeline(pdf_path, ocr="lighton", tts="gemini")
        shutil.move(str(gemini_dir), str(lighton_dir))
        shutil.move(str(tmp), str(gemini_dir))
    else:
        print(f"{lighton_dir} already exists, skipping (delete it to redo from scratch)")

    step("3/8", "Detect disagreements (ensemble_review.py)")
    review_path = ensemble_review.run(gemini_dir, lighton_dir)

    step("4/8", "Resolve disagreements against the real image (vlm_arbiter.py)")
    vlm_arbiter.run(review_path)

    step("5/8", "Apply corrections -- wholesale-replace failed pages, then word-level fixes")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    failed_pages = [int(pn) for pn, r in review["pages"].items() if r and
                    sum(1 for x in r if x["id"].startswith("gemini-only:")) / len(r) >= REPLACE_THRESHOLD]
    if failed_pages:
        print(f"page(s) >= {REPLACE_THRESHOLD:.0%} gemini-only, replacing wholesale: {failed_pages}")
        replace_page.run(review_path, REPLACE_THRESHOLD, None)
    apply_corrections.run(review_path)

    step("6/8", "Re-diacritize + re-synthesize + rebuild reader")
    run_tashkel.run(lighton_dir)
    run_speak_gemini.run(lighton_dir, run_speak_gemini.VOICE)
    build_reader.build(lighton_dir)

    step("7/8", "Catch errors both engines agreed on (context_detector.py)")
    print("this uses a reasoning model -- several minutes per page is normal, not a hang")
    context_review_path = context_detector.run(review_path)

    step("8/8", "Build the review page")
    review_html = build_context_review.run(context_review_path)

    print(f"""
{'='*70}
STOPPING HERE ON PURPOSE -- one manual step left.
{'='*70}

Open {review_html} and check the flagged words against the real page
image, prioritizing "different word" entries over case-ending-only ones.

Then edit {lighton_dir / 'context_decisions.json'}:
  change "reject" to "accept" (use the model's proposal) or to your own
  text, for whichever entries you agree with.

Then run:
  python3 tools/apply_context_suggestions.py "{context_review_path}"
  <vx2lm python> -c "import sys; sys.path.insert(0,'src'); from pipeline import run_tashkel, run_speak_gemini, build_reader; from pathlib import Path; d=Path('{lighton_dir}'); run_tashkel.run(d); run_speak_gemini.run(d, run_speak_gemini.VOICE); build_reader.build(d)"

Result so far: {gemini_dir / 'reader.html'} and {lighton_dir / 'reader.html'}
""")
    return review_html


def main():
    ap = argparse.ArgumentParser(
        description="Run the full OCR-correction chain on one PDF, stopping before the one manual review step.")
    ap.add_argument("pdf", help="path to the book PDF")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"no such file: {pdf_path}")
    run(pdf_path)


if __name__ == "__main__":
    main()
