# Plan: pymupdf import rename

**Spec:** [spec.md](spec.md)

Mechanical rename: `import fitz` → `import pymupdf`, `fitz.` → `pymupdf.`. Also update any
test that monkeypatches `fitz`. Pin the floor version in `service/pyproject.toml` (`ai` extra)
and the root `requirements.txt`.

```
service/ai_engine/adapters/ai/renderer_pymupdf.py
service/ai_engine/adapters/ai/ocr_textlayer.py
src/pipeline/*.py  (grep for fitz)
tools/*.py         (grep for fitz)
service/pyproject.toml, requirements.txt
```
