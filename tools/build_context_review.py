"""Offline step: the review page for every proposed word change in a LightOn book --
context_review.json, vlm_review.json and the automatic fixes in ensemble_review.json,
rendered together, grouped by page with the real page image inline, and a
suspicion-sorted jump list at the top for fast triage.

Three kinds of entry, one decision each (spec 034):

  LLM suggestion    context_detector.py: both engines agreed, a text-only model doubts it.
                    Default "reject": nothing applies until a person accepts it.
  image, not applied  vlm_review.json: a VLM verdict neither OCR engine produced, which
                    apply_corrections.py queues instead of applying (spec 017). Default "reject".
  image fix         apply_corrections.py already applied it. Default "accept" (keep the fix);
                    "reject" undoes it.

Every entry shows the phrase it sits in (the words around it, as the detectors saw them),
every reading, and three controls: keep the OCR word ("reject"), take the proposal
("accept"), or type the word yourself. Decisions go to context_decisions.json; this page
never edits a page record -- tools/apply_context_suggestions.py applies them.

Each entry is also classified as DIFFERENT WORD (the proposal differs from the OCR word even
after normalize_for_compare() folds away diacritics/alif-forms/tatweel -- check the image
first) or CASE-ENDING (only diacritics differ -- often the diacritizer's call, more debatable).

Saving: with --serve the page is served on 127.0.0.1 and Save writes context_decisions.json
directly. Opened as a plain file, Save becomes a download of the same JSON.

Usage:
    python3 tools/build_context_review.py "work/<book>-lighton/context_review.json"
    python3 tools/build_context_review.py "work/<book>-lighton/context_review.json" --serve
"""
import argparse
import html
import json
import os
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ensemble_review import normalize_for_compare, phrase_around  # noqa: E402

KIND_LLM, KIND_QUEUED, KIND_FIX = "llm", "vlm-queue", "fix"
MAX_DECISION_LEN = 200
MAX_POST_BYTES = 1_000_000


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


def collect(lighton_dir: Path, review: dict, ensemble: dict) -> list[dict]:
    """Every reviewable entry, with its phrase, readings and default decision."""
    rows_by_page = ensemble.get("pages", {})
    phrases = {page: {r["id"]: phrase_around(rows, i) for i, r in enumerate(rows)}
               for page, rows in rows_by_page.items()}
    rows_by_id = {r["id"]: r for rows in rows_by_page.values() for r in rows}

    def entry(e: dict, page: str, kind: str) -> dict:
        row = rows_by_id.get(e["id"], {})
        if kind == KIND_LLM:
            reads = {"llm": e["proposed"]}
        else:
            reads = {"lighton": e.get("lighton_txt", row.get("lighton_txt")),
                     "gemini": e.get("gemini_txt", row.get("gemini_txt")), "vlm": e["proposed"]}
        return {
            "id": e["id"], "page": int(page), "kind": kind, "source": e.get("source", ""),
            "ocr_word": e["ocr_word"], "proposed": e["proposed"],
            "suspicion": float(e.get("suspicion", 1.0)), "model": e.get("model") or row.get("vlm_model"),
            "context": e.get("context") or phrases.get(page, {}).get(e["id"], {"before": "", "after": ""}),
            "reads": reads, "default": "accept" if kind == KIND_FIX else "reject",
        }

    out = [entry(e, page, KIND_LLM) for page, es in review["pages"].items() for e in es]
    queued: set[str] = set()
    vlm_path = lighton_dir / "vlm_review.json"
    if vlm_path.exists():
        for page, es in json.loads(vlm_path.read_text(encoding="utf-8"))["pages"].items():
            for e in es:
                queued.add(e["id"])
                out.append(entry(e, page, KIND_QUEUED))
    for page, rows in rows_by_page.items():
        for r in rows:
            verdict = (r.get("verified_txt") or "").strip()
            if (r["action"] != "VERIFIED" or r["id"] in queued or not verdict
                    or normalize_for_compare(verdict) == normalize_for_compare(r["lighton_txt"])):
                continue  # only words apply_corrections.py actually changed
            out.append(entry({"id": r["id"], "ocr_word": r["lighton_txt"], "proposed": verdict,
                              "suspicion": 1.0, "context": r.get("context")}, page, KIND_FIX))
    return out


HTML_TEMPLATE = """<!doctype html><html lang=ar dir=rtl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{book} — مراجعة التصحيحات</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
color:#1a1d21;background:#eef1f4;padding:0 18px 40px;direction:ltr}}
.wrap{{max-width:1100px;margin:0 auto}}
.bar{{position:sticky;top:0;z-index:5;background:#eef1f4;padding:14px 0 10px;display:flex;gap:12px;
align-items:center;flex-wrap:wrap;border-bottom:1px solid #dfe3e8;margin-bottom:18px}}
.bar .count{{font-size:13px;color:#5c646e}}
.bar button{{font:600 14px inherit;padding:7px 16px;border-radius:8px;border:1px solid #1c4f8f;
background:#1c4f8f;color:#fff;cursor:pointer}}
.bar button:disabled{{opacity:.45;cursor:default}}
.bar .status{{font-size:13px}}
.bar .status.ok{{color:#0f6b3a}} .bar .status.err{{color:#a4392f}} .bar .status.dirty{{color:#9a6b00}}
h1{{font-size:24px;font-weight:800;margin:22px 0 4px;direction:rtl;text-align:right}}
.sub{{color:#7a8290;font-size:13px;margin-bottom:16px}}
.howto{{font-size:13px;color:#5c646e;background:#fff;border:1px solid #dfe3e8;border-radius:10px;padding:10px 14px;margin-bottom:16px}}
.howto code{{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:#f1f3f6;padding:1px 5px;border-radius:4px}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:#5c646e;margin-bottom:18px}}
.legend span{{display:flex;align-items:center;gap:6px}}
.legend i{{width:11px;height:11px;border-radius:3px;display:inline-block}}
.jump{{background:#fff;border:1px solid #dfe3e8;border-radius:12px;padding:14px 16px;margin-bottom:22px;overflow-x:auto}}
.jump h2{{font-size:13px;text-transform:uppercase;letter-spacing:.03em;color:#7a8290;margin-bottom:10px}}
.jump table{{width:100%;border-collapse:collapse;font-size:13px}}
.jump th{{text-align:left;color:#7a8290;font-size:11px;text-transform:uppercase;padding:4px 8px}}
.jump td{{padding:5px 8px;border-top:1px solid #eef1f4}}
.jump a{{color:#1c4f8f;text-decoration:none}}
.jump a:hover{{text-decoration:underline}}
.tag{{font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:999px;white-space:nowrap}}
.tag.diff{{background:#fdeceb;color:#a4392f}}
.tag.case{{background:#eaf1fb;color:#1c4f8f}}
.tag.vlm{{background:#f3ecfb;color:#6b3fa0}}
.tag.llm{{background:#eef1f4;color:#4a525c}}
.tag.fix{{background:#e9f6ee;color:#0f6b3a}}
.dec{{font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:999px;white-space:nowrap}}
.dec.reject{{background:#f1f3f6;color:#8a92a0}}
.dec.accept{{background:#eef8f1;color:#0f6b3a}}
.dec.custom{{background:#fdf3e0;color:#9a6b00}}
.page{{background:#fff;border:1px solid #dfe3e8;border-radius:14px;overflow:hidden;margin-bottom:20px}}
.page-hd{{padding:10px 16px;background:#fafbfc;border-bottom:1px solid #eef1f4;font-weight:700;font-size:13px}}
.page-body{{display:flex;align-items:stretch}}
.page-img{{flex:1;min-width:0;max-height:640px;overflow:auto;background:#000}}
.page-img img{{width:100%;display:block}}
.page-list{{flex:1.25;min-width:0;max-height:640px;overflow:auto;padding:6px 14px}}
.group{{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#7a8290;padding:10px 4px 2px}}
.entry{{padding:10px 6px;border-bottom:1px solid #f1f3f6;font-size:14px}}
.entry:last-child{{border-bottom:none}}
.entry.changed{{background:#fffaf0}}
.phrase{{direction:rtl;text-align:right;font-family:'Amiri','Scheherazade New','Geeza Pro',serif;font-size:20px;line-height:1.9}}
.phrase mark{{background:#ffe9a8;padding:0 3px;border-radius:3px}}
.phrase .ctx{{color:#5c646e}}
.reads{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;margin-top:4px;direction:rtl}}
.read{{font-size:12.5px;background:#f6f7f9;border:1px solid #eef1f4;border-radius:6px;padding:1px 8px}}
.read b{{font-family:'Amiri','Scheherazade New','Geeza Pro',serif;font-size:16px;font-weight:600}}
.read small{{color:#7a8290;font-family:-apple-system,'Segoe UI',sans-serif}}
.meta{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px}}
.meta .sus{{font-size:10.5px;color:#8a92a0;font-family:ui-monospace,Menlo,monospace}}
.choose{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px;direction:rtl}}
.choose label{{display:flex;align-items:center;gap:5px;font-size:13px;border:1px solid #dfe3e8;border-radius:8px;padding:3px 9px;cursor:pointer;background:#fff}}
.choose label:has(input:checked){{border-color:#1c4f8f;background:#eaf1fb}}
.choose label b{{font-family:'Amiri','Scheherazade New','Geeza Pro',serif;font-size:16px}}
.choose input[type=text]{{font:16px 'Amiri','Scheherazade New','Geeza Pro',serif;direction:rtl;width:10em;
padding:2px 6px;border:1px solid #c3cbd4;border-radius:6px}}
.choose input[type=text]:disabled{{background:#f6f7f9}}
.choose input:focus-visible,.bar button:focus-visible{{outline:2px solid #1c4f8f;outline-offset:2px}}
@media (max-width:760px){{.page-body{{flex-direction:column}}}}
</style></head><body><div class=wrap>

<div class=bar>
<button id=save type=button disabled>Save decisions</button>
<span class=count id=count></span>
<span class=status id=status></span>
</div>

<h1>{book} — مراجعة التصحيحات</h1>
<div class=sub>{n_total} entr(ies) across {n_pages} page(s) · {n_llm} LLM suggestion(s) · {n_vlm} VLM answer(s) neither engine read · {n_fix} image fix(es) already applied</div>

<div class=howto>Pick a decision for each word, then <b>Save</b>. Nothing changes in the book until you run
<code>python3 tools/apply_context_suggestions.py "{review_path}"</code>, then re-run diacritics, audio and the reader.
<span id=modehint></span></div>

<div class=legend>
<span><i style="background:#fdeceb"></i>different word — check the image first</span>
<span><i style="background:#eaf1fb"></i>diacritics/case-ending only</span>
<span class="dec reject">reject</span> keep the OCR word (undo, for an image fix)
<span class="dec accept">accept</span> take the proposal (keep, for an image fix)
<span class="dec custom">custom</span> your own word
</div>

<div class=jump>
<h2>Sorted by suspicion (highest first)</h2>
<table>
<tr><th>sus</th><th>pg</th><th>word</th><th></th><th>proposed</th><th>type</th><th>decision</th></tr>
{jump_rows}
</table>
</div>

{page_sections}

</div>
<script id=data type="application/json">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const RESERVED = ["accept", "reject"];
const saveB = document.getElementById("save"), statusEl = document.getElementById("status"),
      countEl = document.getElementById("count");
let serverMode = false, dirty = false;

function current(id) {{
  const box = document.querySelector(`.entry[data-id="${{CSS.escape(id)}}"]`);
  const choice = box.querySelector("input[type=radio]:checked").value;
  if (choice !== "custom") return choice;
  return box.querySelector("input[type=text]").value.trim();
}}
function badge(value) {{
  if (value === "accept" || value === "reject") return `<span class="dec ${{value}}">${{value}}</span>`;
  const s = document.createElement("span"); s.className = "dec custom"; s.textContent = "custom: " + value;
  return s.outerHTML;
}}
function refresh() {{
  let changed = 0, bad = 0;
  for (const e of DATA.entries) {{
    const v = current(e.id);
    const box = document.querySelector(`.entry[data-id="${{CSS.escape(e.id)}}"]`);
    const invalid = !v || (box.querySelector("input[value=custom]").checked && RESERVED.includes(v));
    box.querySelector("input[type=text]").setCustomValidity(invalid ? "type a word" : "");
    if (invalid) bad++;
    box.classList.toggle("changed", v !== e.default);
    if (v !== e.default) changed++;
    document.querySelectorAll(`[data-dec="${{CSS.escape(e.id)}}"]`).forEach(el => el.innerHTML = invalid ? "" : badge(v));
  }}
  countEl.textContent = `${{DATA.entries.length}} entries · ${{changed}} differ from the default`;
  saveB.disabled = bad > 0 || (serverMode && !dirty);
  if (bad) {{ statusEl.className = "status err"; statusEl.textContent = `${{bad}} custom field(s) empty`; }}
}}
function collect() {{
  const out = {{}};
  for (const e of DATA.entries) out[e.id] = current(e.id);
  return out;
}}
document.querySelectorAll(".entry").forEach(box => {{
  const text = box.querySelector("input[type=text]");
  box.querySelectorAll("input[type=radio]").forEach(r => r.addEventListener("change", () => {{
    text.disabled = r.value !== "custom" || !r.checked;
    if (!text.disabled) text.focus();
    markDirty();
  }}));
  text.addEventListener("input", markDirty);
}});
function markDirty() {{
  dirty = true; statusEl.className = "status dirty"; statusEl.textContent = "unsaved changes"; refresh();
}}
function apply(decisions) {{
  for (const e of DATA.entries) {{
    const box = document.querySelector(`.entry[data-id="${{CSS.escape(e.id)}}"]`);
    const v = decisions[e.id] ?? e.default;
    const choice = RESERVED.includes(v) ? v : "custom";
    box.querySelector(`input[value=${{choice}}]`).checked = true;
    const text = box.querySelector("input[type=text]");
    text.disabled = choice !== "custom";
    if (choice === "custom") text.value = v;
  }}
}}
saveB.addEventListener("click", async () => {{
  const body = JSON.stringify(collect(), null, 1);
  if (!serverMode) {{
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([body], {{type: "application/json"}}));
    a.download = "context_decisions.json"; a.click();
    statusEl.className = "status ok";
    statusEl.textContent = "downloaded: replace context_decisions.json in the book folder with it";
    dirty = false; return;
  }}
  saveB.disabled = true; statusEl.className = "status"; statusEl.textContent = "saving…";
  try {{
    const r = await fetch("api/decisions", {{method: "POST", headers: {{"Content-Type": "application/json"}}, body}});
    const out = await r.json();
    if (!r.ok) throw new Error(out.error || r.statusText);
    dirty = false; statusEl.className = "status ok";
    statusEl.textContent = `saved ${{out.saved}} decision(s) to context_decisions.json`;
  }} catch (err) {{
    statusEl.className = "status err"; statusEl.textContent = "not saved: " + err.message;
  }}
  refresh();
}});
window.addEventListener("beforeunload", ev => {{ if (dirty) ev.preventDefault(); }});

apply(DATA.decisions);
fetch("api/decisions").then(r => r.ok ? r.json() : Promise.reject()).then(d => {{
  serverMode = true; apply(d); saveB.textContent = "Save decisions";
  document.getElementById("modehint").textContent = " Save writes context_decisions.json directly.";
  refresh();
}}).catch(() => {{
  saveB.textContent = "Download decisions";
  document.getElementById("modehint").textContent =
    " Opened as a file: Save downloads the JSON. Run this tool with --serve to save in place.";
  refresh();
}});
refresh();
</script>
</body></html>"""

KIND_LABEL = {KIND_LLM: ("llm", "LLM suggestion"), KIND_FIX: ("fix", "image fix, applied")}
GROUP_TITLE = {KIND_FIX: "Applied automatically from the page image",
               KIND_QUEUED: "Image answers waiting for you",
               KIND_LLM: "LLM suggestions (both engines agreed)"}
READ_LABEL = {"lighton": "LightOn", "gemini": "Gemini", "vlm": "image verdict", "llm": "LLM"}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _kind_tag(e: dict) -> str:
    if e["kind"] == KIND_QUEUED:
        label = "VLM multi-word" if e["source"] == "vlm-multi-word" else "VLM third answer"
        return f'<span class="tag vlm">{label}</span>'
    cls, label = KIND_LABEL[e["kind"]]
    return f'<span class="tag {cls}">{label}</span>'


def _badge(value: str) -> str:
    if value in ("accept", "reject"):
        return f'<span class="dec {value}">{value}</span>'
    return f'<span class="dec custom">custom: {_esc(value)}</span>'


def _entry_html(e: dict, decision: str) -> str:
    cls = classify(e["ocr_word"], e["proposed"])
    ctx = e["context"]
    reads = "".join(f'<span class=read><small>{READ_LABEL[k]}</small> <b>{_esc(v) or "—"}</b></span>'
                    for k, v in e["reads"].items())
    fix = e["kind"] == KIND_FIX
    keep_label = "Undo fix, back to" if fix else "Keep OCR word"
    take_label = "Keep fix" if fix else "Use proposal"
    name = f"d-{_esc(e['id'])}"
    custom = decision not in ("accept", "reject")
    return f"""<div class=entry data-id="{_esc(e['id'])}">
  <div class=phrase><span class=ctx>{_esc(ctx.get('before'))}</span> <mark>{_esc(e['ocr_word'])}</mark> <span class=ctx>{_esc(ctx.get('after'))}</span></div>
  <div class=reads>{reads}</div>
  <div class=meta>
    <span class="tag {cls}">{'different word' if cls == 'diff' else 'case-ending'}</span>
    {_kind_tag(e)}
    <span data-dec="{_esc(e['id'])}">{_badge(decision)}</span>
    <span class=sus>sus {e['suspicion']:.2f} · {_esc(e['id'])}{' · ' + _esc(e['model']) if e['model'] else ''}</span>
  </div>
  <div class=choose role=radiogroup aria-label="decision for {_esc(e['id'])}">
    <label><input type=radio name="{name}" value=reject{' checked' if decision == 'reject' else ''}> {keep_label} <b>{_esc(e['ocr_word'])}</b></label>
    <label><input type=radio name="{name}" value=accept{' checked' if decision == 'accept' else ''}> {take_label} <b>{_esc(e['proposed'])}</b></label>
    <label><input type=radio name="{name}" value=custom{' checked' if custom else ''}> My word</label>
    <input type=text id="c-{_esc(e['id'])}" aria-label="my word for {_esc(e['id'])}" maxlength={MAX_DECISION_LEN} value="{_esc(decision) if custom else ''}"{'' if custom else ' disabled'}>
  </div>
</div>"""


def run(review_path: Path) -> Path:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    lighton_dir = review_path.parent
    ensemble = json.loads((lighton_dir / "ensemble_review.json").read_text(encoding="utf-8"))
    gemini_dir = Path(ensemble["gemini_dir"])
    if not gemini_dir.is_dir():  # report written before the book tree moved
        gemini_dir = lighton_dir.parent / gemini_dir.name

    decisions_path = lighton_dir / "context_decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.exists() else {}

    entries = collect(lighton_dir, review, ensemble)
    decided = {e["id"]: decisions.get(e["id"], e["default"]) for e in entries}

    jump_rows = []
    for e in sorted(entries, key=lambda e: -e["suspicion"]):
        cls = classify(e["ocr_word"], e["proposed"])
        jump_rows.append(
            f'<tr><td>{e["suspicion"]:.2f}</td><td><a href="#p{e["page"]}">{e["page"]}</a></td>'
            f'<td dir=rtl>{_esc(e["ocr_word"])}</td><td>→</td><td dir=rtl>{_esc(e["proposed"])}</td>'
            f'<td><span class="tag {cls}">{"different word" if cls == "diff" else "case-ending"}</span> {_kind_tag(e)}</td>'
            f'<td data-dec="{_esc(e["id"])}">{_badge(decided[e["id"]])}</td></tr>')

    page_sections = []
    for page_no in sorted({e["page"] for e in entries}):
        on_page = [e for e in entries if e["page"] == page_no]
        groups = []
        for kind in (KIND_FIX, KIND_QUEUED, KIND_LLM):
            of_kind = [e for e in on_page if e["kind"] == kind]
            if of_kind:
                groups.append(f'<div class=group>{GROUP_TITLE[kind]} ({len(of_kind)})</div>'
                              + "".join(_entry_html(e, decided[e["id"]]) for e in of_kind))
        img = find_image(lighton_dir, gemini_dir, page_no)
        img_html = (f'<div class=page-img><img src="{_esc(quote(img))}" alt="page {page_no}"></div>' if img else
                    '<div class=page-img style="display:flex;align-items:center;justify-content:center;color:#8a92a0">no image</div>')
        page_sections.append(f"""<div class=page id="p{page_no}">
<div class=page-hd>page {page_no} — {len(on_page)} to review</div>
<div class=page-body>{img_html}<div class=page-list>{''.join(groups)}</div></div>
</div>""")

    data = {"entries": [{"id": e["id"], "default": e["default"]} for e in entries], "decisions": decided}
    page = HTML_TEMPLATE.format(
        book=_esc(lighton_dir.name), review_path=_esc(review_path), n_total=len(entries),
        n_pages=len({e["page"] for e in entries}),
        n_llm=sum(e["kind"] == KIND_LLM for e in entries),
        n_vlm=sum(e["kind"] == KIND_QUEUED for e in entries),
        n_fix=sum(e["kind"] == KIND_FIX for e in entries),
        jump_rows="\n".join(jump_rows), page_sections="\n".join(page_sections),
        data_json=json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))

    out = lighton_dir / "context_review.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB) -- {len(entries)} entries")
    return out


# ── --serve: save decisions from the page ────────────────────────────────────

def validate_decisions(body: bytes, allowed: set[str]) -> dict[str, str]:
    """The posted decisions, or ValueError naming what is wrong (SC-004)."""
    try:
        posted = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(posted, dict):
        raise ValueError("expected an object of {id: decision}")
    unknown = sorted(set(posted) - allowed)
    if unknown:
        raise ValueError(f"ids not on this page: {unknown[:5]}")
    out = {}
    for id_, value in posted.items():
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_DECISION_LEN \
                or any(ord(c) < 32 for c in value):
            raise ValueError(f"{id_}: a decision is 'accept', 'reject' or one word of text")
        out[id_] = value.strip()
    return out


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class _ReviewHandler(SimpleHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _is_api(self) -> bool:
        return urlsplit(self.path).path.endswith("/api/decisions")

    def _local_host(self) -> bool:
        # a page on another site can't reach this server by rebinding its own name to 127.0.0.1
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self._local_host():
            return self._json(403, {"error": "use http://127.0.0.1"})
        if not self._is_api():
            return super().do_GET()
        path = self.server.decisions_path
        self._json(200, json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})

    def do_POST(self):
        if not self._local_host():
            return self._json(403, {"error": "use http://127.0.0.1"})
        if not self._is_api():
            return self._json(404, {"error": "not found"})
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self._json(403, {"error": "cross-origin request refused"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "send application/json"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_POST_BYTES:
            return self._json(413, {"error": "empty or too large"})
        try:
            posted = validate_decisions(self.rfile.read(length), self.server.allowed_ids)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        path = self.server.decisions_path
        decisions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        decisions.update(posted)  # ids not on the page keep their decision
        _write_atomic(path, decisions)
        self._json(200, {"saved": len(posted)})

    def log_message(self, format, *args):  # quiet: the page reports its own state
        pass


def make_server(review_path: Path, port: int = 8765) -> tuple[ThreadingHTTPServer, str]:
    """Build the page and a server for it on 127.0.0.1. Returns (server, page URL)."""
    review_path = review_path.resolve()
    page = run(review_path)
    lighton_dir = review_path.parent
    ensemble = json.loads((lighton_dir / "ensemble_review.json").read_text(encoding="utf-8"))
    gemini_dir = Path(ensemble["gemini_dir"])
    if not gemini_dir.is_dir():
        gemini_dir = lighton_dir.parent / gemini_dir.name
    root = Path(os.path.commonpath([lighton_dir, gemini_dir.resolve()]))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(_ReviewHandler, directory=str(root)))
    server.decisions_path = lighton_dir / "context_decisions.json"
    server.allowed_ids = {e["id"] for e in collect(lighton_dir, review, ensemble)}
    url = f"http://127.0.0.1:{server.server_address[1]}/{quote(page.relative_to(root).as_posix())}"
    return server, url


def main():
    ap = argparse.ArgumentParser(description="Build the review page for a LightOn book's proposed changes.")
    ap.add_argument("review_json", help="path to context_review.json")
    ap.add_argument("--serve", action="store_true",
                    help="serve the page on 127.0.0.1 so Save writes context_decisions.json")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="with --serve: don't open a browser")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    if not args.serve:
        run(review_path)
        return
    server, url = make_server(review_path, args.port)
    print(f"review page: {url}\nSave writes {server.decisions_path}\nCtrl+C to stop")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
