# SharePoint batch OCR and the benchmark dashboard

How to OCR a whole folder of books from SharePoint, upload the results for the Arabic reviewers,
and keep the managers' benchmark dashboard current.

Two scripts do the work:

| Script | What it does | Costs money? |
|---|---|---|
| `tools/run_folder.py` | OCRs every book in a folder and lays the results out like SharePoint «OCR Results»; `--upload` copies them there | Yes: one Gemini call per page |
| `tools/benchmark.py` | Scans both SharePoint folders and reports where every book stands: status, pages, review readiness, time, cost, audio | No |

**Just want the steps?** [run-a-publisher-folder.md](run-a-publisher-folder.md) is the start-to-finish
runbook for one publisher: plan, batch OCR, upload, cost report, dashboard, clean-up.

The dashboard is **Reading First OCR Benchmark**: <https://claude.ai/artifact/GYm32j3FKrLQ3ERc9m1vmZ>

---

## How the pieces fit

```
SharePoint «Reading First Book Sample»        SharePoint «OCR Results»
  <publisher>/<book>.pdf                         <publisher>/<book>/ocr_check.html, p001.json, ...
          │                                               ▲              │
          │ OneDrive sync (online-only)                   │ --upload     │ OneDrive sync
          ▼                                               │              ▼
   tools/run_folder.py ──► result/ocr_results/<book>/ ────┘      tools/benchmark.py
          │                                                              │
          └── uses src/pipeline/main.py (Gemini OCR)                     ▼
              and tools/build_ocr_check.py (review page)     result/benchmark.json
                                                             result/dashboard/*.json ──► dashboard
```

---

## One-time setup

### 1. OneDrive sync of the two SharePoint folders

1. Install OneDrive: `brew install --cask onedrive` (needs your Mac password), then sign in with your
   `rsi-tech` work account.
2. Open each shared link in the browser and click **Add shortcut to My files**:
   «Reading First Book Sample» and «OCR Results».
3. In Finder, right-click each folder under OneDrive and choose **Free up space**, so files stay
   online-only (cloud icon) and nothing large is downloaded.

They appear here, with the owner's name in front:

```
~/Library/CloudStorage/OneDrive-TheSchoolofResearchScience/
  Moh Roshdey's files - Reading First Book Sample/
  W Ashraf's files - OCR Results/
```

If a shortcut doesn't show up, quit and reopen OneDrive (menu-bar cloud → gear → Quit OneDrive).

> The Azure CLI route (`az login`) is blocked by the organization's sign-in policy. Use OneDrive.

### 2. Python environment

The system `python3` is enough for `tools/benchmark.py`. Anything that OCRs needs the **vx2lm**
conda env (pymupdf, google-genai):

```bash
~/miniconda3/envs/vx2lm/bin/python -c "import pymupdf, google.genai; print('ok')"
```

### 3. Gemini key

`GEMINI_API_KEY` in the repo's `.env` (already set) or in your shell environment.

### Shortcuts used below

```bash
cd ~/Downloads/github_projects/ocr_review_models
PY=~/miniconda3/envs/vx2lm/bin/python
OD=~/Library/CloudStorage/OneDrive-TheSchoolofResearchScience
SRC="$OD/Moh Roshdey's files - Reading First Book Sample"
RES="$OD/W Ashraf's files - OCR Results"
```

---

## Everyday workflows

### A. OCR a publisher folder and deliver it

```bash
# 1. Plan: which books are found, which are already delivered. Free.
$PY tools/run_folder.py "$SRC/دار هلا" --dry-run

# 2. Cheap test: first 2 pages of one book (~$0.01)
$PY tools/run_folder.py "$SRC/دار هلا" --book "الكنز المفقود" --pages 2

# 3. Run the whole folder. Books already in «OCR Results» are skipped.
$PY tools/run_folder.py "$SRC/دار هلا"

# 4. Open a result's review page and look it over
open "result/ocr_results/الكنز المفقود/ocr_check.html"

# 5. Upload the complete books to «OCR Results»
$PY tools/run_folder.py "$SRC/دار هلا" --upload

# 6. Refresh the dashboard (see C)
python3 tools/benchmark.py --dashboard result/dashboard
```

If step 3 stops (Gemini quota, crash, Ctrl-C), run the **same command again**. Finished pages are
never paid for twice.

### B. Check where everything stands (no changes, no cost)

```bash
python3 tools/benchmark.py
```

Prints the totals and writes the full detail to `result/benchmark.json`.

### C. Refresh the dashboard after new files arrive

1. Check the new files show in Finder under the OneDrive folders (a cloud icon is fine).
2. Run:

   ```bash
   python3 tools/benchmark.py --dashboard result/dashboard
   ```

3. Put the two new files on the dashboard, either way:
   - **Ask Claude**: "refresh the benchmark". It runs the scanner, says which books changed,
     uploads both files and checks the page.
   - **Yourself**: open the dashboard → **Sources** → on «Books and their delivery status» replace
     the file with `result/dashboard/books.json`; on «Step times per delivered book» replace it
     with `result/dashboard/step_times.json`.

---

## Command reference

### `tools/run_folder.py`: OCR a folder of books

```
$PY tools/run_folder.py FOLDER [options]
```

| Option | Meaning |
|---|---|
| `FOLDER` | Any folder of books: a publisher folder in the synced source, or a local folder |
| `--dry-run` | List the books and the plan. No API calls, nothing written |
| `--book NAME` | Only this book. Any spelling works («البرمئيات» finds «البرمائيات»). Repeatable |
| `--pages 2` | Pages 1–2 only. Also `13:20` (pages 13–20) or `13:` (13 to the end). Default `0` = all |
| `--full` | Also add diacritics and `reader.html`. Never audio |
| `--redo` | Also run books that are already in «OCR Results» (default: skip them) |
| `--out DIR` | Local results folder. Default `result/ocr_results/` |
| `--upload` | Copy **complete** books into the synced «OCR Results», as `<publisher>/<book>/` (the publisher folder is the source folder's name); OneDrive uploads them |
| `--overwrite` | With `--upload`: replace a book folder that is already there |

Examples:

```bash
# two specific books
$PY tools/run_folder.py "$SRC/مؤسسة حورس" --book "من أكون" --book "خالد بن الوليد"

# a local folder of PDFs
$PY tools/run_folder.py ~/Desktop/new_batch --dry-run

# re-OCR a delivered book and replace it in «OCR Results»
$PY tools/run_folder.py "$SRC/دار الريادة" --book "الأمانة" --redo
$PY tools/run_folder.py "$SRC/دار الريادة" --book "الأمانة" --upload --overwrite

# OCR plus diacritics and reader page
$PY tools/run_folder.py "$SRC/دار نهضة مصر" --full
```

**Default mode** (Gemini OCR → `ocr_check.html`) is what the reviewers need. `--full` takes longer
and adds the diacritics pass and the reader page.

**What gets uploaded.** Only books where every page is OCR'd and `ocr_check.html` exists. A book
already in «OCR Results» is skipped unless you add `--overwrite`. `audio/` is never copied.

**Output per book**, the same files as a book in «OCR Results»:

```
result/ocr_results/<book>/
  book.json          id, title, source as "<folder>/<file>" (no local paths)
  p001.json …        OCR'd page records
  pages/             page images Gemini read
  check_pages/       high-resolution pages for the review page
  ocr_check.html     the Arabic reviewers' page
  reader.html        only with --full
  timings.jsonl      seconds per step
  usage.jsonl        tokens per paid call (for cost)
result/ocr_results/run_folder.log   full log of every run
```

**Summary table** printed at the end:

| Column | Meaning |
|---|---|
| pages | OCR'd pages / pages in the PDF |
| check page | `ocr_check.html` was built |
| OCR time | wall-clock time logged for the book |
| cost | everything logged in its `usage.jsonl`, earlier runs included |
| upload | copied / skipped / not uploaded |
| status | done, partial (run again), or failed (reason; details in the log) |

### Batch mode: half price, results later

`--batch` sends every page that still needs OCR, in all the books of a folder, to Google as
**one Gemini Batch job**: the same model, prompt and settings as the normal run, at half the price
(`gemini-3.8-flash:batch` in `config/prices.toml`). Results come back when the job finishes:
Google targets 24 hours and is usually much quicker (a 6-page test took 3 min 18 s). A job
not finished within 48 hours expires and returns nothing; its pages are simply sent again by the
next `--batch`.

```bash
# 1. plan + cost (half-price figure shown too)
$PY tools/run_folder.py "$SRC/دار نهضة مصر" --batch --dry-run

# 2. cheap test: pages 1-2 of three books, one job
$PY tools/run_folder.py "$SRC/دار نهضة مصر" --book "أنا محبوب" --book "المتاهة" --pages 2 --batch

# 3. the whole folder as one job (your Mac can sleep afterwards)
$PY tools/run_folder.py "$SRC/دار نهضة مصر" --batch

# 4. later: fetch the results, write the pages, build review pages, export
$PY tools/run_folder.py "$SRC/دار نهضة مصر" --collect     # says "not finished yet" until it is

# 5. upload the complete books, as usual
$PY tools/run_folder.py "$SRC/دار نهضة مصر" --upload
```

- A folder can have only one open job: `--batch` refuses to submit again until `--collect` has
  fetched the last one, so nothing is paid for twice. Pages already OCR'd are never sent.
- Each job is recorded in `work/_batches/<folder>/<time>.json` (job name, pages sent, and after
  collecting: final state and turnaround).
- A page Google could not answer is listed by `--collect` and sent again by the next `--batch`.
- `--full` (diacritics + reader) is not done in batch; run it afterwards without `--batch`.

### `tools/benchmark.py`: the benchmark scanner

```
python3 tools/benchmark.py [options]
```

| Option | Meaning |
|---|---|
| *(none)* | Scan both synced folders, print totals, write `result/benchmark.json` |
| `--dashboard DIR` | Also write `DIR/books.json` and `DIR/step_times.json` for the dashboard |
| `--source DIR` | Use another source folder instead of the synced «Reading First Book Sample» |
| `--results DIR` | Use another results folder instead of the synced «OCR Results» |
| `--out FILE` | Write the full detail somewhere other than `result/benchmark.json` |

It reads file names, sizes and dates only. The only files it opens are the small logs in each result
folder (`timings.jsonl`, `usage.jsonl`, `book.json`; about 300 KB in all). PDFs and images are
never downloaded. If a folder isn't synced it stops and says which.

### `src/pipeline/main.py`: one book (what `run_folder.py` calls)

```bash
$PY src/pipeline/main.py "path/to/book.pdf" --ocr-check     # OCR -> ocr_check.html, then stop
$PY src/pipeline/main.py "path/to/book.pdf" --no-tts        # OCR -> diacritics -> reader, no audio
$PY src/pipeline/main.py "path/to/book.pdf"                 # everything, audio included
$PY src/pipeline/main.py "path/to/book.pdf" --pages 13:20   # only pages 13-20
$PY src/pipeline/main.py "path/to/book.pdf" --force-ocr     # OCR even if the PDF has a text layer
```

Work goes to `work/<book>/`. The folder is chosen by the PDF's content, so a renamed copy resumes
the same work. `tools/run_folder.py` uses its own folders, `work/<book>-folder/`, so a folder run never
reuses an older local test of the same PDF (diacritised, spoken, or an older engine) as raw OCR.

### Other tools you may need

```bash
# rebuild a book's review page from work already on disk (no API calls)
$PY tools/build_ocr_check.py "work/<book>" --pdf "path/to/book.pdf"

# what a book's run cost, by stage
python3 tools/cost_report.py "work/<book>"
python3 tools/cost_report.py "result/ocr_results/<book>" --by page
```

---

## How books are counted

Used by both scripts, so the dashboard and the folder runner always agree.

- **A book is one interior PDF.** Files in a publisher folder or a book folder inside it.
- **Covers are never books:**
  - a name containing `cover` or «غلاف»
  - inside a book folder, any PDF next to a «داخلي» (interior) file, e.g. «التلوث المشروع الوطني للقراءة.pdf»
  - inside a book folder, a PDF under 15% of the size of the biggest PDF there, e.g. «jelly fish new.pdf» (1.4 MB) next to a 25 MB interior
- **Duplicates count once.** A loose PDF that repeats a book folder's file is the same book.
- **Titles match across spellings.** Hamza forms, ة/ه, ى/ي, diacritics, «الجزء 2» / «الجزء الثاني»,
  `_merged`, ` - Copy` and words like «داخلي» or «بعد التعديل» are ignored when matching a source
  book to its result folder.
- Titles with only a cover uploaded (no interior PDF) aren't listed at all.

## What the dashboard statuses mean

**Book status**

| Status | Meaning |
|---|---|
| Delivered | Its folder in «OCR Results» has every page OCR'd and a `book.json` |
| In progress | Its folder exists but is incomplete |
| Not started | No result folder yet |

**Arabic review** (from `ocr_check.html` in «OCR Results»)

| Tag | Meaning | What to do |
|---|---|---|
| Ready | Every page with text has a review image | Reviewers can start |
| Page N missing | That text page has no review image | Rebuild the review page and upload with `--overwrite` |
| Needs rebuild | The review page is from an earlier run and doesn't match the OCR | Same |
| Missing | No `ocr_check.html` | Same |
| Not yet | Not OCR'd yet | — |

Pages with no text (pictures only) are left out of review on purpose, so they never show as missing.

**Audio** comes only from local pipeline runs (`work/<book>/audio/`). It isn't in SharePoint yet.

**Links.** "Source PDF" opens the book's interior PDF in «Reading First Book Sample»; "OCR result"
opens its folder in «OCR Results» (review page and page files). They are built from the folder
paths in `tools/benchmark.py` (`SHAREPOINT`, `SOURCE_WEB`, `RESULTS_WEB`); change those if a
folder moves. They open only for people with access to the SharePoint folders.

**Time per page** is wall clock: it includes waiting for Gemini's rate limit and retried calls.
**Cost** is the logged token counts priced with `config/prices.toml`.

---

## Costs and safety

- Each OCR'd page is **one paid Gemini call** (about $0.006 a page with the main pipeline) and sends
  the page image to Google. The books already in «OCR Results» used the «قيمي» 5-sample setup,
  about $0.037 a page.
- Always start with `--dry-run`, then `--pages 2` on one book.
- Running a book downloads its PDF from OneDrive (often 20–100 MB). Afterwards you can right-click
  the folder → **Free up space** again.
- `--upload` writes into W Ashraf's shared folder, which managers see. It only copies complete books
  and never replaces one without `--overwrite`.
- Exported `book.json` files carry `"<folder>/<file>"`, never a path on your Mac.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Not synced yet: Reading First Book Sample, OCR Results` | OneDrive isn't signed in, or a shortcut is missing. Redo setup step 1 |
| New files don't show in Finder | Wait a few minutes, or quit and reopen OneDrive; check the menu-bar icon for errors |
| `pymupdf is missing` | Run with `$PY` (the vx2lm env), not `python3` |
| Run stops partway (quota) | Run the same command again later; it resumes |
| `skipped: … already in OCR Results` | Intended. Use `--redo` to re-OCR and `--upload --overwrite` to replace it |
| A book you expect isn't listed | Only its cover may be uploaded, or its PDF is named like a cover. Check the folder in Finder |
| Dashboard shows old numbers | Refresh it (workflow C). The "Updated" badge shows the scan time |
