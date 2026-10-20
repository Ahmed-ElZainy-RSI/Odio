# Quickstart: OCR Correction

```bash
VX2=~/miniconda3/envs/vx2lm/bin/python3

# everything automatable, stops before the human step
$VX2 tools/run_full_pipeline.py "assets/PDF Samples/book.pdf" --pages 11:15

# or by stage
python3 tools/ensemble_review.py "work/<book>" "work/<book>-lighton"
$VX2    tools/vlm_arbiter.py "work/<book>-lighton/ensemble_review.json"
python3 tools/apply_corrections.py "work/<book>-lighton/ensemble_review.json"
python3 tools/context_detector.py "work/<book>-lighton/ensemble_review.json"
python3 tools/build_context_review.py "work/<book>-lighton/context_review.json"
# edit work/<book>-lighton/context_decisions.json by hand (default: reject), then
python3 tools/apply_context_suggestions.py "work/<book>-lighton/context_review.json"
```

Then re-run `001` stages (tashkel → speak → build_reader); only changed pages are redone.
