"""Rebuild demo/ocr_benchmark_typescript.html from typescript-src/.

What this does, in order:
  1. Compile data.ts + app.ts together with `tsc --strict` (type-checks the
     manifest in data.ts against the shapes defined in app.ts).
  2. Extract MODEL_META (the compiled manifest) back out as JSON, by running
     the compiled JS in Node and printing it.
  3. For each entry in MODEL_META, read models/<file>, strip its Cost: block
     and any image references, and use that as its OCR text.
  4. Render assets/Ibn_Battuta_in_Egypt.pdf to page images (thumb + full).
  5. Assemble typescript-src/shell.html (the page's HTML/CSS) with the fonts,
     the compiled JS, and the model/page data, and write the result to
     demo/ocr_benchmark_typescript.html as a standalone file.

Requirements:
  - Node.js + npx on PATH (used to type-check/compile the .ts files).
  - Python packages: pymupdf, pillow  (pip install pymupdf pillow)

Usage:
  python3 demo/build.py
"""
import base64
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
ROOT = DEMO_DIR.parent
SRC = DEMO_DIR / "typescript-src"
MODELS_DIR = ROOT / "models"
ASSETS_DIR = ROOT / "assets"
SOURCE_PDF = ASSETS_DIR / "Ibn_Battuta_in_Egypt.pdf"
OUTPUT_HTML = DEMO_DIR / "ocr_benchmark_typescript.html"

TSC_FLAGS = [
    "--target", "ES2020", "--lib", "ES2020,DOM", "--strict",
    "--noUncheckedIndexedAccess", "--noImplicitReturns", "--noFallthroughCasesInSwitch",
]


def find_tsc_runner():
    local = SRC / "node_modules" / ".bin" / "tsc"
    if local.exists():
        return [str(local)]
    if shutil.which("npx"):
        return ["npx", "--yes", "-p", "typescript", "tsc"]
    sys.exit("No tsc found: run `npm install` in demo/typescript-src, or install Node/npx.")


def compile_typescript(tmp_dir: Path) -> tuple[str, str]:
    """Returns (data_js, app_js) compiled source, in dependency order."""
    runner = find_tsc_runner()
    dist = tmp_dir / "dist"
    cmd = runner + [str(SRC / "data.ts"), str(SRC / "app.ts"), *TSC_FLAGS, "--outDir", str(dist)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"tsc failed:\n{result.stdout}\n{result.stderr}")
    return (dist / "data.js").read_text(encoding="utf-8"), (dist / "app.js").read_text(encoding="utf-8")


def extract_model_meta(data_js: str, tmp_dir: Path) -> list[dict]:
    """Run the compiled data.js in Node and dump MODEL_META as JSON."""
    probe = tmp_dir / "probe.js"
    probe.write_text(data_js + "\nconsole.log(JSON.stringify(MODEL_META));\n", encoding="utf-8")
    result = subprocess.run(["node", str(probe)], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"Failed to read MODEL_META from data.ts:\n{result.stderr}")
    return json.loads(result.stdout)


def strip_cost_block(text: str) -> str:
    return re.sub(r"Cost:\s*\n```.*?```\s*\n?", "", text, flags=re.DOTALL)


def strip_images(text: str) -> str:
    text = re.sub(r"<div[^>]*>\s*<img[^>]*>\s*</div>", "⟦FIGURE⟧", text)
    text = re.sub(r"<img[^>]*>", "⟦FIGURE⟧", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "⟦FIGURE⟧", text)
    text = re.sub(r"<!--\s*image\s*-->", "⟦FIGURE⟧", text)
    text = re.sub(r"\\?\[IMAGE\\?\]", "⟦FIGURE⟧", text, flags=re.IGNORECASE)
    return text


def clean_ocr_text(raw: str) -> str:
    text = strip_cost_block(raw)
    text = strip_images(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_model_text(model_meta: list[dict]) -> dict[str, str]:
    model_text = {}
    for m in model_meta:
        path = MODELS_DIR / m["file"]
        if not path.exists():
            sys.exit(f"models/{m['file']} (referenced by id \"{m['id']}\" in data.ts) does not exist.")
        model_text[m["id"]] = clean_ocr_text(path.read_text(encoding="utf-8"))
    return model_text


def render_pages() -> list[dict]:
    import fitz
    from PIL import Image

    if not SOURCE_PDF.exists():
        sys.exit(f"{SOURCE_PDF} not found.")

    doc = fitz.open(str(SOURCE_PDF))
    pages = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
        im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

        full = im.copy()
        full.thumbnail((1400, 1400 * 3), Image.LANCZOS)
        full_buf = io.BytesIO()
        full.save(full_buf, "JPEG", quality=78, optimize=True)

        thumb = im.copy()
        thumb.thumbnail((360, 360 * 3), Image.LANCZOS)
        thumb_buf = io.BytesIO()
        thumb.save(thumb_buf, "JPEG", quality=72, optimize=True)

        pages.append({
            "n": i,
            "full": base64.b64encode(full_buf.getvalue()).decode(),
            "thumb": base64.b64encode(thumb_buf.getvalue()).decode(),
        })
    return pages


def b64_font(name: str) -> str:
    return base64.b64encode((SRC / "fonts" / name).read_bytes()).decode()


def build_source_viewer_script() -> str:
    """Populates the "View the full TypeScript source" panel with the real,
    current contents of data.ts and app.ts — not a stale copy."""
    data_ts = (SRC / "data.ts").read_text(encoding="utf-8")
    app_ts = (SRC / "app.ts").read_text(encoding="utf-8")
    combined = (
        "// ============================== data.ts ==============================\n\n"
        + data_ts
        + "\n\n// ============================== app.ts ===============================\n\n"
        + app_ts
    )
    return (
        "\n(function () {\n"
        '  const host = document.getElementById("tsSource");\n'
        f"  host.textContent = {json.dumps(combined, ensure_ascii=False)};\n"
        "})();\n"
    )


def assemble(shell: str, data_js: str, app_js: str, model_meta: list[dict], model_text: dict, pages: list[dict]) -> str:
    js_bundle = (
        data_js
        + "\nconst MODEL_TEXT = " + json.dumps(model_text, ensure_ascii=False) + ";\n"
        + "const PAGES = " + json.dumps(pages) + ";\n"
        + app_js
        + build_source_viewer_script()
    )
    out = shell.replace("__COMPILED_JS__", js_bundle)
    out = out.replace("__FONT_AMIRI__", b64_font("Amiri-subset.woff2"))
    out = out.replace("__FONT_DISPLAY__", b64_font("Fraunces-Display.woff2"))
    out = out.replace("__FONT_SUB__", b64_font("Fraunces-Sub.woff2"))

    title_match = re.match(r"\s*<title>(.*?)</title>\s*", out, re.DOTALL)
    if not title_match:
        sys.exit("shell.html must start with a <title> tag.")
    title = title_match.group(1)
    body = out[title_match.end():]
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>{title}</title>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        print("Compiling TypeScript...")
        data_js, app_js = compile_typescript(tmp_dir)

        print("Reading model manifest...")
        model_meta = extract_model_meta(data_js, tmp_dir)
        print(f"  {len(model_meta)} model(s): {', '.join(m['id'] for m in model_meta)}")

        print("Reading + cleaning OCR text from models/...")
        model_text = build_model_text(model_meta)

        print("Rendering manuscript pages...")
        pages = render_pages()
        print(f"  {len(pages)} page(s)")

        print("Assembling final HTML...")
        shell = (SRC / "shell.html").read_text(encoding="utf-8")
        html = assemble(shell, data_js, app_js, model_meta, model_text, pages)

        OUTPUT_HTML.write_text(html, encoding="utf-8")
        print(f"Wrote {OUTPUT_HTML} ({len(html) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
