# Plan: «قيمي» books OCR into `result/`

**Spec:** [spec.md](spec.md)

```
tools/qiyami/tune_flash.py          the setup: prompt v2, strips(), call() with retries, vote(); RUNS = where raw answers go
tools/qiyami/score_ocr.py           reference text (GT) and score() for الأمانة p4–p5
tools/qiyami/qiyami_run.py          one book: render → 5 samples → vote → result/<book>/   (--book, --rebuild)
tools/qiyami/build_viewers.py       result/<book>/<book>.html + result/كتب_قيمي.html, no API call
tools/qiyami/viewer_template.html   the combined viewer; build_viewers.py fills /*BOOKS*/
work/qiyami/<book>/page_N/          raw model answers (sample_*.raw, voted text), git-ignored
```

Copied from `D:\Full_Project\test_ocr` (`amana_run.py`, `tune_flash.py`, `score_ocr.py`) on 2026-11-11.
Changes: `amana_run.py` → `qiyami_run.py` with repo-relative defaults (`--out result`, `--runs work/qiyami`),
`--pdf` required, the `amana` run folder renamed `الأمانة`; `tune_flash.RUNS` replaces the hard-coded `results_tune`.

**Run** (from the repo root, `GEMINI_API_KEY` set):

```
python tools/qiyami/qiyami_run.py --pdf "books/الأنانية.pdf"
python tools/qiyami/qiyami_run.py --pdf "books/التعاون (1).pdf" --book التعاون
python tools/qiyami/qiyami_run.py --pdf ... --rebuild     # no API call: rebuild result/<book>/ from work/qiyami
python tools/qiyami/build_viewers.py                     # after any run
```

Books are independent: each has its own `work/qiyami/<book>/` and `result/<book>/`, so two books can
run at the same time. Each page sends 5 calls in parallel; `call()` retries 429/500/503 up to 4 times with backoff.

**Cost:** 5 calls per page (each: page + 3 strips at high resolution). 26 + 25 pages = 255 calls.

**Vote:** the sample whose letters agree most with the others gives the letters; each letter takes the
mark set most samples put on the aligned letter. Only the marks are voted; the letters are one sample's.

**Viewers:** the combined page started as the published artifact «كتب قيمي: الصفحة والنص»; in the template
`fetch('books.json')` is replaced by embedded data, image paths point at `result/<book>/images/pNNN.png`, and
the `#book-page` link accepts any book folder (it was fixed to the first three).
