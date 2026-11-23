# Runbook: OCR one publisher folder, start to finish

The exact steps used for «دار هلا», «دار رحيق الكتب», «دار نهضة مصر», «دار نور المعارف» and
«دار المستقبل»: plan and cost, OCR (batch or standard), check, upload to «OCR Results», cost
report, dashboard refresh, local clean-up. Copy the commands in order; only `P` changes.

Background, flags and troubleshooting: [sharepoint-batch-and-benchmark.md](sharepoint-batch-and-benchmark.md).

---

## 0. Every time: open a terminal and set the shortcuts

```bash
cd ~/Downloads/github_projects/ocr_review_models
PY=~/miniconda3/envs/vx2lm/bin/python
OD=~/Library/CloudStorage/OneDrive-TheSchoolofResearchScience
SRC="$OD/Moh Roshdey's files - Reading First Book Sample"
RES="$OD/W Ashraf's files - OCR Results"

P="دار المستقبل"          # the publisher folder to run, exactly as named in «Reading First Book Sample»
```

Run **one publisher at a time**: the run log and the local results (`result/ocr_results/`) are
shared by all runs.

---

## 1. Plan: pages and cost (free)

```bash
$PY tools/run_folder.py "$SRC/$P" --batch --dry-run
```

It copies the folder's PDFs to `work/_sources/$P/` (counting pages needs the file) and prints one
line per book:

| `plan` says | Meaning |
|---|---|
| `run` | Will be OCR'd |
| `resume` | Partly done locally; only the missing pages are paid for |
| `skip: already in OCR Results` | Already delivered (by us or a colleague); nothing to do |
| `skip: cover (1 page)` | A 1–2 page PDF is a cover, never a book |
| `skip: done locally (upload with --upload)` | Finished but not uploaded yet: go to step 4 |
| `skip: could not download (run again)` | OneDrive was busy; run the same command again |

The last lines give the total: `N to run, X pages, about $… ` and the half-price batch figure.

The script's estimate uses $0.007 a page; real costs have been lower, because picture books carry
little text:

| | Measured cost a page | Time |
|---|---|---|
| **Batch** (half price) | $0.002–0.003 | one job; 5–7 min for 168–1,045 pages so far (Google allows up to 24 h) |
| Standard | $0.006–0.007 | about 14 s a page while you wait (168 pages ≈ 40 min) |

Batch and standard give the same quality: the differences between them (mostly diacritics) are the
same size as between two standard runs of the same page. **Use batch** unless you need one book
right now.

---

## 2a. Run with batch (recommended)

```bash
# submit every page that needs OCR, from all books, as one job
$PY tools/run_folder.py "$SRC/$P" --batch
```

It prints the job name and `Collect with: …`. Your Mac can sleep or the terminal close; the job runs
at Google. To collect, either run this whenever you like (it says `not finished yet` until it is):

```bash
$PY tools/run_folder.py "$SRC/$P" --collect
```

or leave this loop running; it checks every 5 minutes and stops once the results are in:

```bash
until out=$($PY tools/run_folder.py "$SRC/$P" --collect 2>&1); echo "$out" | tail -20;
      echo "$out" | grep -qE "^Results:|No open batch jobs"; do sleep 300; done
```

`--collect` writes the pages, builds each book's `ocr_check.html`, and ends with a table:

```
book           this job  pages on disk  check page  cost so far  status
أرنوب المهمل   12 ok     12/12          yes         $0.0251      complete
```

Every book should say `pages on disk` N/N, `check page yes`, `complete`. If a line says
`! <book> pXX: …` (Google refused or garbled a page), see [When a page fails](#when-a-page-fails).

## 2b. Or run standard (pay as you go, results now)

```bash
$PY tools/run_folder.py "$SRC/$P"
```

If it stops (quota, crash, Ctrl-C, the Mac sleeps), run the **same command again**: finished
pages are never paid for twice. To watch it from another terminal:

```bash
tail -f result/ocr_results/run_folder.log
```

---

## 3. Look at the results (free)

```bash
ls result/ocr_results/                                   # one folder per book
open "result/ocr_results/<book>/ocr_check.html"          # the Arabic reviewers' page
```

Each book folder holds `book.json`, `p001.json …`, `pages/`, `check_pages/`, `ocr_check.html`,
`timings.jsonl`, `usage.jsonl`, the same as every book in «OCR Results».

---

## 4. Upload to «OCR Results» and check the upload

```bash
$PY tools/run_folder.py "$SRC/$P" --upload
```

Each complete book is copied to `«OCR Results»/$P/<book>/` (the publisher folder is created if
needed); OneDrive then uploads it. A book already there is **never** replaced unless you add
`--overwrite`. Incomplete books are listed as `not uploaded, not complete yet`.

Check OneDrive has really uploaded every file (repeat until it prints `0`):

```bash
find "$RES/$P" -type f ! -name '.*' -print0 | while IFS= read -r -d '' f; do
  fileproviderctl evaluate "$f" 2>/dev/null | grep -q 'isUploaded = 1' || echo "NOT UPLOADED: ${f#$RES/}"
done | tee /dev/stderr | wc -l | xargs echo "files not uploaded:"
```

---

## 5. Cost report (free)

```bash
python3 tools/folder_costs.py "$P"                       # from the local results
mv result/ocr_results/run_folder.log "result/runs/$P/run_folder.log"
```

Prints pages, cost and cost a page for each book and the total, and writes every page's tokens and
cost to `result/runs/$P/cost_per_page.csv` (opens in Excel). Run it **before** step 7, which deletes
the local results. After clean-up you can still price what is in «OCR Results», including books a
colleague delivered:

```bash
python3 tools/folder_costs.py "$P" --books "$RES/$P" --out /tmp/costs.csv
```

---

## 6. Refresh the dashboard

```bash
python3 tools/benchmark.py --dashboard result/dashboard
```

The summary should show the publisher's books as `delivered` and the total number of books
unchanged. Then put the two new files on the dashboard
(<https://claude.ai/artifact/GYm32j3FKrLQ3ERc9m1vmZ>):

- **Ask Claude**: "refresh the dashboard". It uploads both files and swaps them in.
- **Yourself**: dashboard → **Sources** → «Books and their delivery status» ← `result/dashboard/books.json`;
  «Step times per delivered book» ← `result/dashboard/step_times.json`.

Batch-run books show no step times on the dashboard: Google does the work in one job, so there are
no per-step timings to log.

---

## 7. Remove the local copies

**Only after step 4 printed `files not uploaded: 0` and step 5 wrote the CSV.**

```bash
rm -rf result/ocr_results/*/          # the local results (now in «OCR Results»)
rm -rf "work/_sources/$P"             # the downloaded PDFs
rm -rf work/*-folder                  # run_folder's working folders
```

zsh may answer `no matches found` when there is nothing left to delete; that is fine.

Keep `result/runs/$P/` (costs and log), `work/_batches/` (batch job records),
`result/page_counts.json` (page counts that tell books from covers) and every other folder in
`work/` (older pipeline tests, some with the audio shown on the dashboard).

---

## Check that nothing is missing (free)

```bash
python3 tools/benchmark.py
```

`status` should show every book `delivered` and `in source` all `yes`; `publishers` lists books per
publisher. A publisher missing from that list has only covers (as «دار الفاروق»: 34 one-page PDFs).

---

## When a page fails

`--collect` lists it as `! <book> pXX: …` and the book stays `partial`.

1. Send the missing pages again: `$PY tools/run_folder.py "$SRC/$P" --batch` (only missing pages go),
   then `--collect`. Or run it now, standard: `$PY tools/run_folder.py "$SRC/$P" --book "<book>"`.
2. If Google refuses the same page every time (it happened once: page 31 of «الرياضيات في المنزل»,
   `block_reason OTHER` at every size), the page needs hand work: OCR it in parts (for example the
   upright half and the upside-down answers half separately) and merge. Ask Claude to do it; then
   upload that book with `--upload --overwrite`.

## Good to know

- **Older local tests are never reused.** run_folder works in its own folders, `work/<book>-folder/`,
  so a book once tried by hand (with diacritics, audio or an older engine) is OCR'd fresh.
- **Only one batch job per publisher at a time.** `--batch` refuses while one is waiting; collect it
  first. A job not finished in 48 hours expires; its pages are sent again by the next `--batch`.
- **Batch prices** are half the standard ones until 2026-12-31 and double from 2027-01-01
  (`config/prices.toml`).
- **OneDrive errors** (`Errno 60`, `could not download`): OneDrive is busy uploading; wait a few
  minutes and run the same command again.
