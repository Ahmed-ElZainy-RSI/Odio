# Plan: content-based book id

**Spec:** [spec.md](spec.md)

`src/pipeline/books.py`: `resolve_work_dir(pdf_path) -> Path`, `book_id(pdf_path)`,
`migrate(work_root)`. `main.py` and `tools/run_full_pipeline.py` call `resolve_work_dir`
instead of building `work/{stem}`. The `-lighton` sibling convention becomes
`resolve_work_dir(pdf, variant="lighton")`.

```
src/pipeline/books.py              new
src/pipeline/main.py               use resolve_work_dir
tools/run_full_pipeline.py         use resolve_work_dir (gemini + lighton dirs)
src/tests/test_books.py            rename → same dir; duplicate → reuse
```
