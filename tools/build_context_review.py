"""Offline step: a browsable HTML review page for context_detector.py's
findings -- context_review.json plus context_decisions.json rendered
together, grouped by page with the real page image inline, and a
suspicion-sorted jump list at the top for fast triage.

Nothing here writes anything. This exists purely to make reviewing
context_decisions.json faster than reading raw JSON: every entry is
classified as DIFFERENT WORD (the proposal differs from the OCR word even
after normalize_for_compare() folds away diacritics/alif-forms/tatweel --
i.e. it isn't just a case-ending call, worth checking against the image
first) or CASE-ENDING (only diacritics differ -- often the automated
diacritizer's error, not the OCR engines', and more debatable). The current
decision from context_decisions.json (if any) is shown so review progress
is visible at a glance.

vlm_review.json entries, when present, are merged in and tagged: VLM
verdicts neither OCR engine produced, which apply_corrections.py queues
for a human instead of applying (spec 017). They carry suspicion 1.0, so
they sort to the top of the jump list.

Usage:
    python3 tools/build_context_review.py "work/<book>-lighton/context_review.json"
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ensemble_review import normalize_for_compare  # noqa: E402


def classify(ocr_word: str, proposed: str) -> str:
    return "diff" if normalize_for_compare(ocr_word) != normalize_for_compare(proposed) else "case"


def find_image(lighton_dir: Path, gemini_dir: Path, page_no: int) -> str | None:
    own = lighton_dir / "pages" / f"p{page_no:03d}.jpg"
    if own.exists():
        return os.path.relpath(own, start=lighton_dir)
    gem = gemini_dir / "pages" / f"p{page_no:03d}.jpg"
    if gem.exists():
        return os.path.relpath(gem, start=lighton_dir)
    return None


HTML_TEMPLATE = """<!doctype html><html lang=ar dir=rtl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{book} — مراجعة الأخطاء المتطابقة</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
color:#1a1d21;background:#eef1f4;padding:26px 18px;direction:ltr}}
.wrap{{max-width:1000px;margin:0 auto}}
h1{{font-size:24px;font-weight:800;margin-bottom:4px;direction:rtl;text-align:right}}
.sub{{color:#7a8290;font-size:13px;margin-bottom:20px}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:#5c646e;margin-bottom:18px}}
.legend span{{display:flex;align-items:center;gap:6px}}
.legend i{{width:11px;height:11px;border-radius:3px;display:inline-block}}
.jump{{background:#fff;border:1px solid #dfe3e8;border-radius:12px;padding:14px 16px;margin-bottom:22px}}
.jump h2{{font-size:13px;text-transform:uppercase;letter-spacing:.03em;color:#7a8290;margin-bottom:10px}}
.jump table{{width:100%;border-collapse:collapse;font-size:13px}}
.jump th{{text-align:left;color:#7a8290;font-size:11px;text-transform:uppercase;padding:4px 8px}}
.jump td{{padding:5px 8px;border-top:1px solid #eef1f4}}
.jump a{{color:#1c4f8f;text-decoration:none}}
.jump a:hover{{text-decoration:underline}}
.tag{{font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:999px}}
.tag.diff{{background:#fdeceb;color:#a4392f}}
.tag.case{{background:#eaf1fb;color:#1c4f8f}}
.tag.vlm{{background:#f3ecfb;color:#6b3fa0}}
.dec{{font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:999px}}
.dec.reject{{background:#f1f3f6;color:#8a92a0}}
.dec.accept{{background:#eef8f1;color:#0f6b3a}}
.dec.custom{{background:#fdf3e0;color:#9a6b00}}
.page{{background:#fff;border:1px solid #dfe3e8;border-radius:14px;overflow:hidden;margin-bottom:20px}}
.page-hd{{padding:10px 16px;background:#fafbfc;border-bottom:1px solid #eef1f4;font-weight:700;font-size:13px}}
.page-body{{display:flex;align-items:stretch}}
.page-img{{flex:1;min-width:0;max-height:420px;overflow:auto;background:#000}}
.page-img img{{width:100%;display:block}}
.page-list{{flex:1;min-width:0;max-height:420px;overflow:auto;padding:10px 14px}}
.entry{{padding:8px 6px;border-bottom:1px solid #f1f3f6;font-size:14px;direction:rtl}}
.entry:last-child{{border-bottom:none}}
.entry .words{{display:flex;gap:8px;align-items:baseline;justify-content:flex-end;font-family:'Amiri','Scheherazade New',serif;font-size:19px}}
.entry .arrow{{color:#8a92a0;font-size:13px}}
.entry .meta{{display:flex;gap:6px;justify-content:flex-end;margin-top:4px;direction:ltr}}
.entry .sus{{font-size:10.5px;color:#8a92a0;font-family:ui-monospace,Menlo,monospace}}
@media (max-width:760px){{.page-body{{flex-direction:column}}}}
</style></head><body><div class=wrap>

<h1>{book} — مراجعة الأخطاء المتطابقة (context_detector.py)</h1>
<div class=sub>{n_total} suspicious word(s) across {n_pages} page(s) · {n_vlm} VLM answer(s) neither engine read · sorted by suspicion in the jump list, grouped by page below</div>

<div class=legend>
<span><i style="background:#fdeceb"></i>different word — check the image first</span>
<span><i style="background:#eaf1fb"></i>diacritics/case-ending only</span>
<span class="dec reject">reject</span> default, unreviewed
<span class="dec accept">accept</span> takes the model's proposal
<span class="dec custom">custom</span> reviewer wrote their own text
</div>

<div class=jump>
<h2>Sorted by suspicion (highest first)</h2>
<table>
<tr><th>sus</th><th>pg</th><th>word</th><th></th><th>proposed</th><th>type</th><th>decision</th></tr>
{jump_rows}
</table>
</div>

{page_sections}

</div></body></html>"""


def run(review_path: Path) -> Path:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    lighton_dir = review_path.parent
    ensemble = json.loads((lighton_dir / "ensemble_review.json").read_text(encoding="utf-8"))
    gemini_dir = Path(ensemble["gemini_dir"])

    decisions_path = lighton_dir / "context_decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.exists() else {}

    pages: dict[str, list[dict]] = {p: list(v) for p, v in review["pages"].items()}
    vlm_path = lighton_dir / "vlm_review.json"
    if vlm_path.exists():
        for page_str, entries in json.loads(vlm_path.read_text(encoding="utf-8"))["pages"].items():
            pages.setdefault(page_str, []).extend(entries)

    all_entries = []
    for page_str, entries in pages.items():
        for e in entries:
            all_entries.append({**e, "page": int(page_str)})
    all_entries.sort(key=lambda e: -float(e["suspicion"]))
    n_vlm = sum(1 for e in all_entries if e.get("source", "").startswith("vlm-"))

    def source_tag(e: dict) -> str:
        source = e.get("source", "")
        if not source.startswith("vlm-"):
            return ""
        label = "VLM multi-word" if source == "vlm-multi-word" else "VLM third answer"
        return f'<span class="tag vlm" title="lighton: {e.get("lighton_txt")} · gemini: {e.get("gemini_txt")}">{label}</span>'

    def decision_badge(id_: str) -> str:
        d = decisions.get(id_, "reject")
        if d == "reject":
            return '<span class="dec reject">reject</span>'
        if d == "accept":
            return '<span class="dec accept">accept</span>'
        return f'<span class="dec custom">custom: {d}</span>'

    jump_rows = []
    for e in all_entries:
        cls = classify(e["ocr_word"], e["proposed"])
        jump_rows.append(
            f'<tr><td>{e["suspicion"]:.2f}</td><td><a href="#p{e["page"]}">{e["page"]}</a></td>'
            f'<td dir=rtl>{e["ocr_word"]}</td><td>→</td><td dir=rtl>{e["proposed"]}</td>'
            f'<td><span class="tag {cls}">{"different word" if cls=="diff" else "case-ending"}</span>{source_tag(e)}</td>'
            f'<td>{decision_badge(e["id"])}</td></tr>'
        )

    page_sections = []
    for page_str in sorted(pages.keys(), key=int):
        entries = pages[page_str]
        if not entries:
            continue
        page_no = int(page_str)
        img = find_image(lighton_dir, gemini_dir, page_no)
        rows = []
        for e in entries:
            cls = classify(e["ocr_word"], e["proposed"])
            rows.append(f"""<div class=entry>
  <div class=words><span>{e['ocr_word']}</span><span class=arrow>&lt;-</span><span>{e['proposed']}</span></div>
  <div class=meta>
    <span class="tag {cls}">{'different word' if cls=='diff' else 'case-ending'}</span>
    {source_tag(e)}
    {decision_badge(e['id'])}
    <span class=sus>sus {e['suspicion']:.2f} · {e['id']}</span>
  </div>
</div>""")
        img_html = f'<div class=page-img><img src="{img}" alt=""></div>' if img else \
            '<div class=page-img style="display:flex;align-items:center;justify-content:center;color:#8a92a0">no image</div>'
        page_sections.append(f"""<div class=page id="p{page_no}">
<div class=page-hd>page {page_no} — {len(entries)} suspicious</div>
<div class=page-body>{img_html}<div class=page-list>{''.join(rows)}</div></div>
</div>""")

    html = HTML_TEMPLATE.format(
        book=lighton_dir.name, n_total=len(all_entries), n_vlm=n_vlm,
        n_pages=sum(1 for v in pages.values() if v),
        jump_rows="\n".join(jump_rows), page_sections="\n".join(page_sections))

    out = lighton_dir / "context_review.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB) -- {len(all_entries)} entries")
    return out


def main():
    ap = argparse.ArgumentParser(description="Build a browsable HTML review page for context_review.json.")
    ap.add_argument("review_json", help="path to context_review.json")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    run(review_path)


if __name__ == "__main__":
    main()
