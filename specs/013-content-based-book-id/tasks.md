# Tasks: content-based book id

- [x] T001 [P] Tests — `src/tests/test_books.py` (9): new PDF → readable dir + index entry + `book.json`; renamed copy → same dir; variants share one entry; a **different PDF with the same filename** gets its own dir instead of writing into the other book's work; an existing dir made from the same PDF is adopted; a deleted dir is recreated; `migrate` is idempotent and skips a dir mixing two PDFs; `run_full_pipeline` passes each pass its own dir
- [x] T002 `src/pipeline/books.py`: `book_id` (first 16 hex chars of SHA-256, deliberately equal to the `pdf_sha256_16` every page record already carries), `resolve_work_dir` (lookup by content in `work/index.json`, per variant), `migrate` (index existing books from their own records), CLI `migrate|show`
- [x] T003 Wired: `main.run_pipeline` defaults its work dir to `books.resolve_work_dir` and takes an explicit `work_dir`; `tools/run_full_pipeline.py` resolves both dirs by content and passes each pass its own. **The old move-aside dance is gone** (rename the Gemini result away, run LightOn into its path, rename back), which left the folders swapped if anything crashed in between. The env-driven `run_*.py` drivers keep accepting `BOOK=`
- [ ] T004 Run `python3 src/pipeline/books.py migrate work/` once `work/` is back on disk

Deviations from the plan:
- Id length 16, not 12, to match the hash prefix already stored in every page record, which is what makes `migrate` possible.
- No `work/by-id/` symlinks: the index maps id → dir directly, and directory names stay readable.
- Behaviour change in `run_full_pipeline`: an existing `-lighton` dir is no longer skipped wholesale. The LightOn pass re-runs, and its drivers skip every page already done (008/009 resume), so finished pages cost nothing and unfinished ones now get completed.

Verification (2026-10-21, vx2lm): `src/tests` 168 passed (was 159); system Python 146 + 14 skipped. The two `I001` notes on `main.py`/`run_full_pipeline.py` exist on `dev` already.
Branch: `feature/013-content-based-book-id`, merged into `dev`.
