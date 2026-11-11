"""HTML viewers for the OCR results in result/ (spec 035, FR-006). No API calls.

    python tools/qiyami/build_viewers.py              # every book folder in result/
    python tools/qiyami/build_viewers.py --out DIR    # another results folder

Writes:
    result/<book>/<book>.html   one book: every page image beside its OCR text, one long page
    result/كتب_قيمي.html        all books together: tabs, page rail, image beside text (viewer_template.html)
Book data is embedded, so both open from disk; images stay in result/<book>/images/.
"""
import argparse
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

CSS = """
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e0d8;--accent:#1f6f5c}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1e1e1e;--ink:#ececec;--muted:#9a9a9a;--line:#333;--accent:#5cc4a6}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Naskh Arabic","Amiri","Traditional Arabic","Segoe UI",Tahoma,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{margin:0;font-size:1.4rem}
header .meta{color:var(--muted);font-size:.9rem}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-inline-start:auto}
nav a{color:var(--accent);text-decoration:none;font-size:.85rem;padding:2px 7px;border:1px solid var(--line);border-radius:6px;font-family:system-ui,sans-serif}
main{max-width:1400px;margin:0 auto;padding:16px}
section{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:20px;scroll-margin-top:80px}
section h2{grid-column:1/-1;margin:0;font-size:1rem;color:var(--muted);display:flex;gap:12px;font-family:system-ui,sans-serif}
img{width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
.text{font-size:1.45rem;line-height:2.1;white-space:pre-wrap;margin:0}
@media (max-width:800px){section{grid-template-columns:1fr}}
"""


def load(book_dir: Path):
    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    pages = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((book_dir / "json").glob("p*.json"))]
    return book, pages


def book_page(book_dir: Path, book: dict, pages: list) -> Path:
    title = html.escape(book["title"])
    nav = "".join(f'<a href="#p{d["page"]}">{d["page"]}</a>' for d in pages)
    body = []
    for d in pages:
        n, sc = d["page"], d.get("score")
        extra = f' · تشكيل {sc["tashkeel"]:.1%}' if sc else ""
        body.append(
            f'<section id="p{n}"><h2><span>صفحة {n}</span><span>{len(d["tokens"])} كلمة · '
            f'نسبة التشكيل {d["diac_ratio"]:.2f}{extra}</span></h2>'
            f'<img loading="lazy" src="{html.escape(d["image"])}" alt="صفحة {n}">'
            f'<p class="text">{html.escape(d["text"])}</p></section>')
    out = book_dir / f'{book["title"]}.html'
    out.write_text(
        f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
        f'<style>{CSS}</style></head><body><header><h1>{title}</h1>'
        f'<span class="meta">{book["pages"]} صفحة · {html.escape(book["ocr_engine"])} · {book["date"]}</span>'
        f'<nav>{nav}</nav></header><main>{"".join(body)}</main></body></html>', encoding="utf-8")
    return out


def combined_page(root: Path, books: list) -> Path:
    data = []
    for book_dir, book, pages in books:
        data.append({"key": book_dir.name, "title": book["title"], "pages": [{
            "n": d["page"], "img": f'{book_dir.name}/{d["image"]}',
            "w": (d.get("render_size") or [1, 1])[0], "h": (d.get("render_size") or [1, 1])[1],
            "words": len(d["tokens"]), "diac": d["diac_ratio"], "score": d.get("score"), "text": d["text"],
        } for d in pages]})
    tpl = (HERE / "viewer_template.html").read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out = root / "كتب_قيمي.html"
    out.write_text(tpl.replace("/*BOOKS*/[]", js, 1), encoding="utf-8")
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "result"))
    root = Path(ap.parse_args().out)
    books = []
    for d in sorted(root.iterdir()):
        if (d / "book.json").exists():
            book, pages = load(d)
            books.append((d, book, pages))
            print(book_page(d, book, pages))
    print(combined_page(root, books))


if __name__ == "__main__":
    main()
