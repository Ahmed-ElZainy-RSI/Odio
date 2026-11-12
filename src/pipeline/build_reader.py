"""Layer 08 — the reader. Consumes only the page index; no pipeline logic here.

box_level "none" (LightOn's OCR path has no geometry) means tokens carry no
`box` field of their own. If ensemble_review.json exists (the book was run
through tools/ensemble_review.py against a Gemini OCR of the same PDF), a
page can still get a compare view by BORROWING Gemini's box and page image
for that page -- Gemini's OCR always renders+saves pages/pXXX.jpg and a per-
word box, regardless of box_level, unlike LightOn's /parse which returns
neither. The borrow only takes for a page if EVERY current LightOn token on
it re-aligns to a Gemini token (via a fresh, on-the-fly alignment -- see
_borrow_boxes below); a partially-boxed page falls back to text-only rather
than half-working, matching the plain-per-page rule this reader already
used for native boxes.

Whether a given page shows its image is a PER-PAGE decision, carried in each
page's "compare" flag (an image exists, native or borrowed); "boxed" adds word
boxes on top when the page also has them. A LightOn page with its own rendered
image but no geometry is therefore compare=True, boxed=False. The two-column
DOM is rendered once if ANY page has an image; load() hides the image column
for pages that don't.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ensemble_review import align_page  # noqa: E402
from pipeline import timing  # noqa: E402

# reader labels for timings.jsonl steps, in pipeline order; unknown steps are shown as-is
TIMING_STEPS = [("render", "render"), ("ocr", "OCR"), ("box-fit", "box fit"),
                ("diacritise", "tashkeel"), ("tts", "TTS"), ("align", "align")]


def _timing_view(work_dir: Path, pages: list[dict]):
    """(steps, per-page {step: s}, book_total) for the pages the reader shows."""
    by_page = timing.latest_by_page(work_dir)
    known = [k for k, _ in TIMING_STEPS]
    seen = {s for p in pages for s in by_page.get(p["page"], {})}
    steps = [s for s in known if s in seen] + sorted(seen - set(known))
    return steps, {p["page"]: by_page.get(p["page"], {}) for p in pages}, timing.latest_book_total(work_dir)


def _borrow_boxes(lighton_tokens: list[dict], gemini_dir: Path, page_no: int) -> dict[str, list[float]]:
    """Fresh, on-the-fly alignment (never persisted) between the CURRENT
    LightOn tokens for one page and that page's Gemini tokens, purely to
    look up a box for each LightOn token via the Gemini token it aligns to.

    Runs the same align_page() tools/ensemble_review.py uses for the
    correction report, but on today's (possibly already-corrected) LightOn
    text -- not the report's saved snapshot -- and doesn't write anything;
    ensemble_review.json's own history of what was flagged/verified is left
    untouched. Returns {} if the Gemini page doesn't exist or coverage isn't
    complete for every current LightOn token.
    """
    gemini_page = gemini_dir / f"p{page_no:03d}.json"
    if not gemini_page.exists():
        return {}
    gemini_tokens = json.loads(gemini_page.read_text(encoding="utf-8"))["tokens"]
    gemini_box_by_id = {t["id"]: t["box"] for t in gemini_tokens if "box" in t}

    boxes = {}
    for r in align_page(gemini_tokens, lighton_tokens):
        gid = r.get("gemini_id")
        if gid and gid in gemini_box_by_id:
            boxes[r["id"]] = gemini_box_by_id[gid]
    if len(boxes) != len(lighton_tokens):
        return {}  # partial coverage -- stay text-only rather than half-working
    return boxes


def build(work_dir: Path):
    """Build work_dir/reader.html from every 'spoken' page record. Returns the output Path, or None if nothing is spoken yet."""
    book = work_dir.name

    # tools/ensemble_review.py writes this separately and never touches
    # pXXX.json -- reading it here is optional and additive, so a book with
    # no review still builds exactly as before.
    review_path = work_dir / "ensemble_review.json"
    review_by_id = {}
    gemini_dir = None
    if review_path.exists():
        review = json.loads(review_path.read_text(encoding="utf-8"))
        gemini_dir = Path(review["gemini_dir"])
        # ensemble_review.json records gemini_dir as it stood when the review
        # ran (e.g. "work/<book>"). Once a book tree is moved or renamed that
        # path stops resolving, which silently costs the -lighton variants
        # every page image they borrow. Fall back to the sibling directory of
        # the same name, which is where the pair actually lives.
        if not gemini_dir.is_dir():
            sibling = work_dir.parent / gemini_dir.name
            if sibling.is_dir():
                gemini_dir = sibling
        for results in review["pages"].values():
            for r in results:
                # "gemini-only:..." ids have no corresponding LightOn token
                # to attach a flag to -- Gemini saw a word LightOn dropped
                # entirely, not a per-token disagreement to highlight
                if r["action"] in ("FLAG", "VERIFIED") and not r["id"].startswith("gemini-only:"):
                    review_by_id[r["id"]] = r

    pages = []
    skipped = []
    for f in sorted(work_dir.glob("p*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        # A page is rendered only when its JSON record is spoken and its audio
        # file genuinely exists; anything less is skipped and reported, never
        # shown half-working. A page image is optional: LightOn's OCR path saves
        # none, and such a page gets the text-only view (compare=False).
        # run_extract.py's text-layer records predate box_level and never set it —
        # they always have word boxes, so an absent key defaults to "word", not "none"
        has_boxes = r.get("box_level", "word") not in (None, "none")
        own_image = work_dir / "pages" / f"p{r['page']:03d}.jpg"
        img_path, has_image, borrowed = f"pages/p{r['page']:03d}.jpg", own_image.exists(), {}
        gemini_jpg = gemini_dir / "pages" / f"p{r['page']:03d}.jpg" if gemini_dir is not None else None

        if not has_boxes and gemini_dir is not None:
            borrowed = _borrow_boxes(r["tokens"], gemini_dir, r["page"])
            if borrowed and gemini_jpg.exists():
                has_boxes = True
            elif borrowed:
                borrowed = {}

        # LightOn's own OCR path never renders/saves page images at all (see
        # module docstring), so a page with NATIVE boxes -- e.g. one
        # replace_page.py wholesale-swapped from Gemini -- still has no
        # local jpg to show them against. Fall back to Gemini's rendered
        # page, which is where those boxes actually came from either way.
        if not has_image and gemini_jpg is not None and gemini_jpg.exists():
            img_path = os.path.relpath(gemini_jpg, start=work_dir)
            has_image = True
        # a page with zero whisper/hume anchors (e.g. heavy OCR noise or digit-vs-
        # spoken-word mismatch) leaves every token with no "t" at all -- not just
        # a rare gap between anchors. Those tokens still display; they just can't
        # highlight or be clicked-to-seek.
        tok = []
        for t in r["tokens"]:
            entry = {"i": t["id"], "r": t["raw"], "t": t["txt"],
                      "m": t.get("t"), "s": t.get("t_src", "untimed"), "f": t["fixes"]}
            if t.get("evidence"):
                # the latest correction only, compact: what the inspector shows (spec 018)
                ev = t["evidence"][-1]
                entry["e"] = {"o": ev["original"], "x": ev["detector"],
                              "d": ev["decided_by"], "r": ev["rule"], "at": ev["at"][:10]}
            rv = review_by_id.get(t["id"])
            if rv:
                # gemini_txt/verified_txt are only added when a review row
                # exists at all, so the reader can tell "never reviewed"
                # (key absent) apart from "reviewed, Gemini had no reading"
                # (key present, value null)
                entry["g"] = rv["gemini_txt"]
                if rv["action"] == "VERIFIED":
                    entry["v"] = rv["verified_txt"]
            tok.append(entry)
        if borrowed:
            for t in tok:
                t["b"] = borrowed[t["i"]]
        elif has_boxes:
            for t, src in zip(tok, r["tokens"]):
                t["b"] = src["box"]
        audio_rel = r.get("audio")
        why = []
        if r.get("status") != "spoken":
            # a corrected page is reset to extracted/diacritised; its old audio file is
            # still on disk, but it speaks the words from before the correction
            why.append("not spoken since its last change")
        elif not audio_rel or not (work_dir / audio_rel).exists():
            why.append("no audio file")
        elif not r.get("duration_ms"):
            why.append("audio has no duration")
        if why:
            skipped.append((r["page"], r.get("status", "?"), " + ".join(why)))
            continue

        pages.append({"page": r["page"], "audio": audio_rel,
            "img": img_path, "dur": r["duration_ms"],
            "diac": r["diac_ratio"], "box_level": r.get("box_level", "word"),
            "has_image": has_image, "compare": has_image, "boxed": has_boxes and has_image,
            "voice": r.get("voice", ""), "tokens": tok})

    if not pages:
        total = len(list(work_dir.glob("p*.json")))
        print(f"{work_dir.name}: 0 of {total} pages are complete (need a spoken JSON + audio file) "
              f"— nothing to build.")
        for pg, st, why in skipped:
            print(f"    p{pg:03d}  [{st}]  {why}")
        return None

    # per-page "compare" already decides native vs. borrowed vs. neither;
    # the DOM only needs the two-column structure if at least one page uses it
    t_steps, t_pages, t_book = _timing_view(work_dir, pages)
    for p in pages:
        p["tm"] = {k: round(t_pages[p["page"]][k], 2) for k in t_steps if k in t_pages[p["page"]]}
    t_label = dict(TIMING_STEPS)
    t_timed = [p for p in pages if p["tm"]]
    t_work = sum(sum(p["tm"].values()) for p in t_timed)
    t_rows = "".join(
        f"<tr><td>{p['page']}</td>"
        + "".join(f"<td>{timing.fmt(p['tm'][s]) if s in p['tm'] else '–'}</td>" for s in t_steps)
        + f"<td><b>{timing.fmt(sum(p['tm'].values()))}</b></td></tr>"
        for p in t_timed)
    t_foot = ("<tr><td>total</td>" + "".join(
        f"<td>{timing.fmt(sum(p['tm'].get(s, 0) for p in t_timed))}</td>" for s in t_steps)
        + f"<td><b>{timing.fmt(t_work)}</b></td></tr>") if t_timed else ""
    timing_html = (
        f"<details class=tmg><summary>Processing time per page ({len(t_timed)}/{len(pages)} pages timed"
        f"{f', book run {timing.fmt(t_book)} wall clock' if t_book else ''})</summary>"
        f"<table><thead><tr><th>pg</th>{''.join(f'<th>{t_label.get(s, s)}</th>' for s in t_steps)}"
        f"<th>page total</th></tr></thead><tbody>{t_rows}</tbody><tfoot>{t_foot}</tfoot></table>"
        "<p>Wall clock, including rate-limit waits and retries. Each page shows the latest run "
        "that processed it; pages made before timing existed have no times.</p></details>"
    ) if t_timed else ""
    t_labels_js = json.dumps(t_label, ensure_ascii=False)

    has_compare = any(p["compare"] for p in pages)
    n_compare = sum(1 for p in pages if p["compare"])
    voice = pages[0]["voice"]
    data = json.dumps(pages, ensure_ascii=False, separators=(",", ":"))
    n_tok = sum(len(p["tokens"]) for p in pages)
    n_timed = sum(1 for p in pages for t in p["tokens"] if t["m"])
    n_anc = sum(1 for p in pages for t in p["tokens"] if t["s"] == "anchor")
    n_interp = sum(1 for p in pages for t in p["tokens"] if t["s"] == "interpolated")
    n_fix = sum(1 for p in pages for t in p["tokens"] if t["f"])
    n_flag = sum(1 for p in pages for t in p["tokens"] if "g" in t and "v" not in t)
    n_verified = sum(1 for p in pages for t in p["tokens"] if "v" in t)
    dur = sum(p["dur"] for p in pages) / 1000

    # ---- pieces assembled outside the big f-string, to keep it readable ----
    meta_bits = [f"{len(pages)} pages", f"{n_tok} words", f"{dur/60:.1f} min of audio"]
    if voice:
        meta_bits.append(f"voice {voice}")
    if has_compare and n_compare < len(pages):
        meta_bits.append(f"page image available for {n_compare}/{len(pages)} pages")
    elif not has_compare:
        meta_bits.append("text view only — no page image available")
    meta_html = "".join(f"<span class=chip>{b}</span>" for b in meta_bits)

    legend = [
        ("lg-now", "Word being spoken", "the gliding highlight follows the voice"),
        ("lg-read", "Already read", "words turn blue once they have been spoken"),
        ("lg-interp", "Estimated timing", "dotted underline: timing guessed between two measured words"),
        ("lg-fixed", "Cleaned by a rule", "a mechanical fix (e.g. stray kashida) was applied"),
    ]
    if n_flag:
        legend.append(("lg-flag", "Engines disagree", "two OCR engines read this word differently; unresolved"))
    if n_verified:
        legend.append(("lg-ver", "Checked against the image", "engines disagreed; a vision model read the page"))
    legend_html = "".join(
        f"<div class=lgi><i class={c}></i><div><b>{t}</b><span>{d}</span></div></div>" for c, t, d in legend)

    def tile(value: str, label: str, count: int | None = None) -> str:
        attr = f" data-count={count}" if count is not None else ""
        return f"<div class=tile><b{attr}>{value}</b><span>{label}</span></div>"

    tiles = [
        tile(str(n_anc), "words anchored to real timestamps"
             + (f" ({100*n_anc/n_timed:.0f}% of timed)" if n_timed else ""), n_anc),
        tile(str(n_interp), "words with timing interpolated between anchors", n_interp),
        tile(str(n_fix), "words cleaned by an extraction rule", n_fix),
    ]
    if n_flag:
        tiles.append(tile(str(n_flag), f"unresolved disagreements ({100*n_flag/n_tok:.0f}%)", n_flag))
    if n_verified:
        tiles.append(tile(str(n_verified), f"VLM-verified disagreements ({100*n_verified/n_tok:.0f}%)", n_verified))
    tiles.append(tile(f"{dur/60:.1f} min", "real synthesised audio"))
    if t_book:
        tiles.append(tile(timing.fmt(t_book), "processing time, last full run (wall clock)"))
    if t_timed:
        tiles.append(tile(timing.fmt(t_work / len(t_timed)), f"processing per page, average of {len(t_timed)}"))
    tiles_html = "".join(tiles)

    stage_html = (
        '<div class=compare id=compare><div class=txtcol id=txtcol><div class=txt id=txt></div></div>'
        '<div class=imgcol><div class=imgw id=imgw><img id=img alt=""><div id=boxes></div>'
        '<div class=bmark id=bmark></div></div></div></div>'
        if has_compare else '<div class=txtcol id=txtcol><div class=txt id=txt></div></div>')

    html = f"""<!doctype html><html lang=ar dir=rtl data-theme=light><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{book} — قارئ متزامن</title>
<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Inter:wght@400;500;600;700&display=swap">
<script>try{{const t=localStorage.getItem("reader-theme"); if(t) document.documentElement.dataset.theme=t;}}catch(e){{}}</script>
<style>
:root{{--bg:#f6f7fb;--surface:#ffffff;--surface-2:#f1f3f8;--line:#e4e7ee;--ink:#121826;--ink-2:#4a5466;--mute:#8a93a4;
--accent:#3157d5;--accent-2:#6d4dd6;--accent-soft:#e9eeff;--read:#3157d5;
--hl:#ffe58a;--hl-line:#f2bf33;--fix:#e9f8ef;--fix-line:#9fd8b4;--flag:#fdecec;--flag-line:#eba79f;--ver:#fff3df;--ver-line:#e9c27c;
--ok:#12804f;--warn:#a1610b;--bad:#c2352b;--shadow:0 1px 2px rgba(16,24,40,.05),0 8px 24px rgba(16,24,40,.07);
--glass:rgba(255,255,255,.78);color-scheme:light}}
:root[data-theme=dark]{{--bg:#0c1016;--surface:#151a22;--surface-2:#1b222c;--line:#2a323e;--ink:#e8edf4;--ink-2:#b3bdca;--mute:#7f8a99;
--accent:#7aa2ff;--accent-2:#a98bff;--accent-soft:#1d2a47;--read:#8fb0ff;
--hl:rgba(255,214,90,.22);--hl-line:#f5c64a;--fix:#123323;--fix-line:#2f7a52;--flag:#3a1a19;--flag-line:#a9473f;--ver:#3a2b12;--ver-line:#a77b2c;
--ok:#4fd197;--warn:#f0b452;--bad:#ff8a7f;--shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
--glass:rgba(21,26,34,.8);color-scheme:dark}}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{font:15px/1.55 Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:var(--ink);
background:var(--bg);padding-bottom:120px;transition:background .35s,color .35s}}
body::before{{content:"";position:fixed;inset:-20% -10% auto;height:60vh;z-index:-1;pointer-events:none;
background:radial-gradient(60% 60% at 70% 20%,color-mix(in srgb,var(--accent) 14%,transparent),transparent 70%),
radial-gradient(50% 50% at 20% 0%,color-mix(in srgb,var(--accent-2) 12%,transparent),transparent 70%)}}
button{{font:inherit;color:inherit}}
.ltr{{direction:ltr}}

/* top bar */
.top{{position:sticky;top:0;z-index:20;backdrop-filter:saturate(1.4) blur(14px);-webkit-backdrop-filter:saturate(1.4) blur(14px);
background:var(--glass);border-bottom:1px solid var(--line)}}
.top-in{{max-width:{'1440px' if has_compare else '1040px'};margin:0 auto;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
.brand{{display:flex;align-items:center;gap:12px;min-width:0}}
.logo{{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;flex:none;color:#fff;
background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:0 6px 18px color-mix(in srgb,var(--accent) 35%,transparent)}}
.logo svg{{width:22px;height:22px}}
h1{{font-family:Amiri,'Geeza Pro',serif;font-size:26px;line-height:1.2;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.meta{{display:flex;gap:6px;flex-wrap:wrap;margin-top:3px;direction:ltr;justify-content:flex-end}}
.chip{{font-size:11.5px;color:var(--ink-2);background:var(--surface-2);border:1px solid var(--line);padding:1px 9px;border-radius:999px;white-space:nowrap}}
.icon-btn{{width:40px;height:40px;border-radius:12px;border:1px solid var(--line);background:var(--surface);cursor:pointer;display:grid;place-items:center;flex:none;
transition:transform .2s,border-color .2s,background .2s}}
.icon-btn:hover{{border-color:var(--accent);transform:translateY(-1px)}}
.icon-btn svg{{width:19px;height:19px}}
.icon-btn .moon,:root[data-theme=dark] .icon-btn .sun{{display:none}}
:root[data-theme=dark] .icon-btn .moon{{display:block}}

.pg{{max-width:{'1440px' if has_compare else '1040px'};margin:0 auto;padding:18px 16px 0}}

/* page strip */
.pages{{display:flex;gap:6px;overflow-x:auto;padding:4px 2px 10px;direction:ltr;scrollbar-width:thin}}
.pages button{{position:relative;flex:none;min-width:40px;height:36px;padding:0 10px;border-radius:10px;border:1px solid var(--line);
background:var(--surface);cursor:pointer;font-size:13px;font-weight:600;color:var(--ink-2);overflow:hidden;
transition:transform .25s cubic-bezier(.2,.8,.2,1),background .25s,color .25s,border-color .25s,box-shadow .25s}}
.pages button:hover{{transform:translateY(-2px);border-color:var(--accent)}}
.pages button i{{position:absolute;left:0;bottom:0;height:3px;width:0;background:var(--accent);opacity:.55;transition:width .4s}}
.pages button.seen i{{width:100%}}
.pages button.on{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;border-color:transparent;
box-shadow:0 6px 16px color-mix(in srgb,var(--accent) 35%,transparent);transform:translateY(-2px)}}
.pages button.on i{{background:#fff}}
.ptm{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;direction:ltr;font-size:12px;color:var(--mute);margin:2px 0 12px}}
.ptm:empty{{display:none}}
.ptm .k{{background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:2px 10px;color:var(--ink-2)}}
.ptm .k b{{color:var(--ink);font-variant-numeric:tabular-nums}}
.ptm .k.tot{{border-color:color-mix(in srgb,var(--accent) 45%,var(--line));color:var(--accent)}}

/* reading stage */
.card{{background:var(--surface);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);overflow:hidden;
transition:background .35s,border-color .35s}}
.compare{{display:flex;align-items:stretch}}
.compare>div{{flex:1;min-width:0}}
.txtcol{{max-height:70vh;overflow:auto;position:relative;scroll-behavior:smooth}}
.compare .txtcol{{border-left:1px solid var(--line)}}
.imgcol{{max-height:70vh;overflow:auto;background:var(--surface-2);padding:14px}}
.compare.textonly .imgcol{{display:none}}
.compare.textonly .txtcol{{border-left:none}}
@media (max-width:820px){{.compare{{flex-direction:column}} .compare .txtcol{{border-left:none;border-bottom:1px solid var(--line);max-height:55vh}} .imgcol{{max-height:none}}}}
.txt{{position:relative;padding:34px 38px;font-size:28px;line-height:2.5;font-family:Amiri,'Scheherazade New','Noto Naskh Arabic','Geeza Pro',serif;min-height:240px}}
@media (max-width:600px){{.txt{{padding:22px 18px;font-size:24px}}}}
.txt.in-next{{animation:inNext .45s cubic-bezier(.2,.8,.2,1)}}
.txt.in-prev{{animation:inPrev .45s cubic-bezier(.2,.8,.2,1)}}
@keyframes inNext{{from{{opacity:0;transform:translateX(-26px)}}to{{opacity:1;transform:none}}}}
@keyframes inPrev{{from{{opacity:0;transform:translateX(26px)}}to{{opacity:1;transform:none}}}}
.marker{{position:absolute;z-index:0;border-radius:10px;background:var(--hl);box-shadow:0 0 0 2px var(--hl-line),0 6px 18px color-mix(in srgb,var(--hl-line) 35%,transparent);
opacity:0;pointer-events:none;transition:left .22s cubic-bezier(.2,.8,.2,1),top .22s cubic-bezier(.2,.8,.2,1),width .22s,height .22s,opacity .2s}}
.marker.on{{opacity:1}}
.w{{position:relative;z-index:1;padding:3px 5px;border-radius:8px;cursor:pointer;scroll-margin:120px 0;
transition:color .3s,background .2s,transform .2s;display:inline-block;line-height:1.5}}
.w:hover{{background:var(--accent-soft)}}
.w.read{{color:var(--read)}}
.w.now{{color:var(--ink);transform:scale(1.06)}}
.w.interp{{text-decoration:underline dotted var(--mute);text-underline-offset:9px;text-decoration-thickness:2px}}
.w.fixed{{background:var(--fix);box-shadow:inset 0 0 0 1px var(--fix-line)}}
.w.flagged{{background:var(--flag);box-shadow:inset 0 0 0 1px var(--flag-line)}}
.w.verified{{background:var(--ver);box-shadow:inset 0 0 0 1px var(--ver-line)}}
.w.now.fixed,.w.now.flagged,.w.now.verified{{background:transparent}}
.w.untimed{{opacity:.5;cursor:default}}
.w.pick{{outline:2px solid var(--accent);outline-offset:1px}}
.imgw{{position:relative;line-height:0;border-radius:12px;overflow:hidden;box-shadow:var(--shadow)}}
.imgw img{{width:100%;display:block;transition:opacity .35s}}
.imgw img.loading{{opacity:0}}
.bx{{position:absolute;border-radius:4px;cursor:pointer;transition:background .2s,box-shadow .2s}}
.bx:hover{{background:color-mix(in srgb,var(--accent) 18%,transparent);box-shadow:0 0 0 2px var(--accent)}}
.bmark{{position:absolute;border-radius:5px;pointer-events:none;opacity:0;background:rgba(255,213,79,.32);
box-shadow:0 0 0 2.5px #f2b51f,0 0 18px rgba(242,181,31,.55);
transition:left .22s cubic-bezier(.2,.8,.2,1),top .22s cubic-bezier(.2,.8,.2,1),width .22s,height .22s,opacity .2s}}
.bmark.on{{opacity:1;animation:pulse 1.6s ease-in-out infinite}}
@keyframes pulse{{50%{{box-shadow:0 0 0 2.5px #f2b51f,0 0 28px rgba(242,181,31,.8)}}}}

/* inspector */
.insp{{direction:ltr;border-top:1px solid var(--line);background:var(--surface-2);padding:14px 18px;min-height:76px;font-size:13px;color:var(--ink-2)}}
.insp .hint{{color:var(--mute);display:flex;align-items:center;gap:8px}}
.insp .row1{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.insp .word{{font-family:Amiri,'Geeza Pro',serif;font-size:30px;line-height:1.3;color:var(--ink);direction:rtl}}
.insp .chips{{display:flex;gap:6px;flex-wrap:wrap}}
.insp .c{{font-size:11.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--line);background:var(--surface)}}
.insp .c.ok{{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line))}}
.insp .c.warn{{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line))}}
.insp .c.bad{{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,var(--line))}}
.insp .c.acc{{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 40%,var(--line))}}
.insp .tech{{margin-top:6px;font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--mute)}}
.insp .note{{margin-top:6px;font-size:12.5px}}
.insp .note b{{font-family:Amiri,'Geeza Pro',serif;font-size:17px;color:var(--ink)}}
.insp.flash{{animation:flash .5s}}
@keyframes flash{{from{{background:var(--accent-soft)}}}}

/* sections below */
.sec{{margin-top:26px}}
.sec h2{{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);font-weight:600;margin:0 2px 10px;direction:ltr;text-align:left}}
.legend{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;direction:ltr}}
.lgi{{display:flex;gap:10px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:11px 13px}}
.lgi i{{flex:none;width:30px;height:20px;border-radius:6px;margin-top:2px}}
.lgi b{{display:block;font-size:13px;font-weight:600}}
.lgi span{{font-size:12px;color:var(--mute)}}
.lg-now{{background:var(--hl);box-shadow:0 0 0 2px var(--hl-line)}}
.lg-read{{background:linear-gradient(90deg,var(--read),var(--accent-2))}}
.lg-interp{{border-bottom:2px dotted var(--mute);height:12px!important}}
.lg-fixed{{background:var(--fix);box-shadow:inset 0 0 0 1px var(--fix-line)}}
.lg-flag{{background:var(--flag);box-shadow:inset 0 0 0 1px var(--flag-line)}}
.lg-ver{{background:var(--ver);box-shadow:inset 0 0 0 1px var(--ver-line)}}
.keys{{display:flex;gap:14px;flex-wrap:wrap;direction:ltr;margin-top:10px;font-size:12.5px;color:var(--ink-2)}}
kbd{{font:600 11px ui-monospace,Menlo,monospace;background:var(--surface);border:1px solid var(--line);border-bottom-width:2px;border-radius:6px;padding:1px 6px;margin-right:4px;color:var(--ink)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;direction:ltr}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px 16px;position:relative;overflow:hidden;
transition:transform .25s,box-shadow .25s}}
.tile:hover{{transform:translateY(-3px);box-shadow:var(--shadow)}}
.tile::after{{content:"";position:absolute;inset:auto 0 0 0;height:3px;background:linear-gradient(90deg,var(--accent),var(--accent-2));opacity:.7}}
.tile b{{display:block;font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.01em}}
.tile span{{font-size:12px;color:var(--mute)}}
.tmg{{margin-top:10px;background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:12px 16px;direction:ltr;font-size:12.5px}}
.tmg summary{{cursor:pointer;font-weight:600;color:var(--ink-2);list-style:none;display:flex;align-items:center;gap:8px}}
.tmg summary::before{{content:"";width:8px;height:8px;border-right:2px solid var(--mute);border-bottom:2px solid var(--mute);transform:rotate(-45deg);transition:transform .25s}}
.tmg[open] summary::before{{transform:rotate(45deg)}}
.tmg table{{border-collapse:collapse;margin-top:12px;width:100%;font-variant-numeric:tabular-nums;animation:fadeUp .35s}}
.tmg th,.tmg td{{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line)}}
.tmg th{{color:var(--mute);font-weight:600;font-size:11.5px}}
.tmg th:first-child,.tmg td:first-child{{text-align:left}}
.tmg tbody tr:hover{{background:var(--surface-2)}}
.tmg tfoot td{{border-top:2px solid var(--line);border-bottom:0;font-weight:600}}
.tmg p{{margin-top:8px;color:var(--mute);font-size:11.5px}}
.reveal{{opacity:0;transform:translateY(14px);transition:opacity .6s,transform .6s cubic-bezier(.2,.8,.2,1)}}
.reveal.shown{{opacity:1;transform:none}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(8px)}}}}

/* player dock */
.dock{{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:30;width:min(920px,calc(100% - 24px));
display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:22px;border:1px solid var(--line);
background:var(--glass);backdrop-filter:saturate(1.5) blur(18px);-webkit-backdrop-filter:saturate(1.5) blur(18px);box-shadow:var(--shadow);
animation:dockIn .6s cubic-bezier(.2,.8,.2,1)}}
@keyframes dockIn{{from{{opacity:0;transform:translate(-50%,30px)}}}}
.dock .nav{{width:38px;height:38px;border-radius:12px;border:1px solid var(--line);background:var(--surface);cursor:pointer;display:grid;place-items:center;flex:none;transition:transform .2s,border-color .2s}}
.dock .nav:hover:not(:disabled){{border-color:var(--accent);transform:scale(1.06)}}
.dock .nav:disabled{{opacity:.35;cursor:default}}
.dock .nav svg{{width:18px;height:18px}}
.play{{width:52px;height:52px;border-radius:50%;border:0;cursor:pointer;flex:none;display:grid;place-items:center;color:#fff;
background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:0 8px 22px color-mix(in srgb,var(--accent) 45%,transparent);
transition:transform .2s cubic-bezier(.2,.8,.2,1),box-shadow .2s}}
.play:hover:not(:disabled){{transform:scale(1.08)}}
.play:active:not(:disabled){{transform:scale(.94)}}
.play:disabled{{opacity:.4;cursor:default}}
.play svg{{width:22px;height:22px}}
.play .pause,body.playing .play .pl{{display:none}}
body.playing .play .pause{{display:block}}
body.playing .play{{animation:glow 2.2s ease-in-out infinite}}
@keyframes glow{{50%{{box-shadow:0 8px 30px color-mix(in srgb,var(--accent) 70%,transparent)}}}}
.mid{{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}}
.mid-top{{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:var(--ink-2)}}
.pl-label{{display:flex;align-items:center;gap:8px;font-weight:600;color:var(--ink)}}
.eq{{display:inline-flex;gap:2px;align-items:flex-end;height:12px}}
.eq i{{width:3px;height:3px;border-radius:2px;background:var(--accent);transition:height .2s}}
body.playing .eq i{{animation:eq 0.9s ease-in-out infinite}}
body.playing .eq i:nth-child(2){{animation-delay:-.3s}} body.playing .eq i:nth-child(3){{animation-delay:-.6s}}
@keyframes eq{{0%,100%{{height:3px}}50%{{height:12px}}}}
.tm{{font-variant-numeric:tabular-nums;direction:ltr;color:var(--mute)}}
.scrub{{position:relative;height:8px;border-radius:999px;background:var(--surface-2);border:1px solid var(--line);cursor:pointer}}
.scrub i{{position:absolute;top:0;bottom:0;right:0;width:0;border-radius:999px;background:linear-gradient(270deg,var(--accent),var(--accent-2));display:block}}
.scrub i::after{{content:"";position:absolute;left:-7px;top:50%;width:14px;height:14px;margin-top:-7px;border-radius:50%;background:#fff;
box-shadow:0 0 0 3px var(--accent),0 2px 6px rgba(0,0,0,.2);transform:scale(0);transition:transform .2s}}
.scrub:hover i::after,body.playing .scrub i::after{{transform:scale(1)}}
.opts{{display:flex;gap:6px;flex:none;direction:ltr}}
.pillbtn{{height:32px;padding:0 11px;border-radius:10px;border:1px solid var(--line);background:var(--surface);cursor:pointer;font-size:12px;font-weight:600;color:var(--ink-2);transition:all .2s}}
.pillbtn:hover{{border-color:var(--accent)}}
.pillbtn.on{{background:var(--accent-soft);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}}
@media (max-width:640px){{.opts .auto{{display:none}} .dock{{gap:8px;padding:8px 10px}} .play{{width:46px;height:46px}}}}
@media (prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;transition:none!important;scroll-behavior:auto!important}} .reveal{{opacity:1;transform:none}}}}
</style></head><body>

<header class=top><div class=top-in>
<div class=brand><div class=logo><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H2z"/><path d="M22 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z"/></svg></div>
<div style="min-width:0"><h1>{book}</h1><div class=meta>{meta_html}</div></div></div>
<button class=icon-btn id=theme title="Switch light / dark" aria-label="Switch light or dark mode">
<svg class=sun viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
<svg class=moon viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
</button></div></header>

<main class=pg>
<nav class=pages id=pgsel aria-label="Pages"></nav>
<div class=ptm id=ptm></div>

<section class=card>
{stage_html}
<div class=insp id=insp><div class=hint>Hover or tap a word to see how it was read and timed. Tap a word to jump the audio there.</div></div>
</section>

<section class="sec reveal"><h2>How to read this page</h2>
<div class=legend>{legend_html}</div>
<div class=keys><span><kbd>Space</kbd>play / pause</span><span><kbd>←</kbd>next page</span><span><kbd>→</kbd>previous page</span><span>tap a word to jump to it</span></div>
</section>

<section class="sec reveal"><h2>About this book</h2>
<div class=tiles>{tiles_html}</div>
{timing_html}
</section>
</main>

<div class=dock dir=rtl>
<button class=nav id=prev title="Previous page" aria-label="Previous page"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
<button class=play id=play title="Play / pause (Space)" aria-label="Play or pause">
<svg class=pl viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.2-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>
<svg class=pause viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg></button>
<button class=nav id=next title="Next page" aria-label="Next page"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg></button>
<div class=mid><div class=mid-top><span class=pl-label><span class=eq><i></i><i></i><i></i></span><span id=plabel></span></span><span class=tm id=tm>0.00 / 0.00</span></div>
<div class=scrub id=scrub><i id=bar></i></div></div>
<div class=opts><button class=pillbtn id=speed title="Reading speed">1×</button><button class="pillbtn auto on" id=auto title="Go on to the next page when a page ends">Auto ▸</button></div>
</div>
<audio id=au preload=metadata></audio>
<script>
const P={data};
const TL={t_labels_js};
const fmtS=s=>{{if(s<60)return s.toFixed(1)+"s"; const r=Math.round(s);
  return Math.floor(r/60)+"m"+String(r%60).padStart(2,"0")+"s";}};
const HAS_COMPARE_LAYOUT={str(has_compare).lower()};
const RM=matchMedia("(prefers-reduced-motion: reduce)").matches;
const store={{get(k){{try{{return localStorage.getItem(k)}}catch(e){{return null}}}},set(k,v){{try{{localStorage.setItem(k,v)}}catch(e){{}}}}}};
let cur=0, last=-1, raf=0, auto=store.get("reader-auto")!=="0";
const SPEEDS=[0.75,1,1.25,1.5]; let speed=+(store.get("reader-speed")||1); if(!SPEEDS.includes(speed)) speed=1;
const $=s=>document.querySelector(s);
const au=$("#au"),txt=$("#txt"),txtcol=$("#txtcol"),compare=$("#compare"),img=$("#img"),boxes=$("#boxes"),bmark=$("#bmark"),
      insp=$("#insp"),bar=$("#bar"),tm=$("#tm"),playB=$("#play");
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");
const seen=new Set();

function showTiming(p){{
  const el=$("#ptm"), t=p.tm||{{}}, ks=Object.keys(t);
  el.innerHTML=ks.length?`<span>page ${{p.page}} took</span>`+ks.map(k=>`<span class=k>${{TL[k]||k}} <b>${{fmtS(t[k])}}</b></span>`).join("")
    +`<span class="k tot">total <b>${{fmtS(ks.reduce((a,k)=>a+t[k],0))}}</b></span>`:"";
}}
function renderPages(){{
  $("#pgsel").innerHTML=P.map((p,i)=>`<button data-p="${{i}}" class="${{i===cur?"on":""}} ${{seen.has(i)?"seen":""}}" title="page ${{p.page}}">${{p.page}}<i></i></button>`).join("");
  const on=$("#pgsel .on"); if(on) on.scrollIntoView({{block:"nearest",inline:"center",behavior:RM?"auto":"smooth"}});
}}
$("#pgsel").onclick=e=>{{const b=e.target.closest("button"); if(b) load(+b.dataset.p);}};

function load(i, keepPlaying){{
  if(i<0||i>=P.length) return;
  const dir=i>=cur?"in-next":"in-prev";
  cur=i; last=-1; const p=P[i];
  showTiming(p);
  // A page without audio must not set au.src -- an empty/missing src fires a
  // network error and leaves the previous page's audio loaded and playable.
  au.pause();
  if(p.audio){{ au.src=p.audio; au.playbackRate=speed; }} else {{ au.removeAttribute("src"); au.load(); }}
  playB.disabled=!p.audio;
  $("#scrub").style.opacity=p.audio?"":"0.35";
  txt.innerHTML='<div class=marker id=marker></div>';
  if(HAS_COMPARE_LAYOUT){{
    compare.classList.toggle("textonly",!p.compare);
    if(p.compare){{ img.classList.add("loading"); img.onload=()=>img.classList.remove("loading"); img.src=p.img; boxes.innerHTML=""; }}
    if(bmark) bmark.classList.remove("on");
  }}
  p.tokens.forEach((t,j)=>{{
    const s=document.createElement("span");
    s.className="w"+(t.s==="interpolated"?" interp":"")+(t.f.length?" fixed":"")
      +("g" in t&&!("v" in t)?" flagged":"")+("v" in t?" verified":"")+(t.m?"":" untimed");
    s.dataset.j=j; s.textContent=t.t;
    txt.appendChild(s); txt.appendChild(document.createTextNode(" "));
    if(p.boxed){{
      const b=document.createElement("div");
      b.className="bx"; b.dataset.j=j;
      b.style.cssText=`left:${{t.b[0]}}%;top:${{t.b[1]}}%;width:${{t.b[2]}}%;height:${{t.b[3]}}%`;
      boxes.appendChild(b);
    }}
  }});
  txt.classList.remove("in-next","in-prev"); void txt.offsetWidth; txt.classList.add(dir);
  if(txtcol) txtcol.scrollTop=0;
  renderPages();
  $("#prev").disabled=!i; $("#next").disabled=i===P.length-1;
  $("#plabel").textContent=`Page ${{p.page}} · ${{i+1}} of ${{P.length}}`;
  insp.innerHTML='<div class=hint>Hover or tap a word to see how it was read and timed. Tap a word to jump the audio there.</div>';
  wire(); render();
  if(keepPlaying&&p.audio) au.play().catch(()=>{{}});
}}
function wire(){{
  document.querySelectorAll(".w,.bx").forEach(el=>{{
    const j=+el.dataset.j;
    el.onmouseenter=()=>{{show(j); pick(j,true);}};
    el.onmouseleave=()=>pick(j,false);
    el.onclick=()=>{{show(j); const m=P[cur].tokens[j].m;
      if(m&&P[cur].audio){{au.currentTime=m[0]/1000; render();}}}};
  }});
}}
function pick(j,on){{ const w=txt.querySelectorAll(".w")[j]; if(w) w.classList.toggle("pick",on); }}
function show(j){{
  const t=P[cur].tokens[j];
  const src=t.s==="anchor"
    ? '<span class="c ok">real timestamp</span>'
    : t.s==="interpolated"
    ? '<span class="c warn">timing estimated between neighbours</span>'
    : '<span class="c bad">no timing available</span>';
  const when=t.m?`<span class=c>${{(t.m[0]/1000).toFixed(2)}}s → ${{(t.m[1]/1000).toFixed(2)}}s</span>`:"";
  const fx=t.f.length?`<span class="c acc">cleaned: ${{esc(t.f.join(", "))}}</span>`:'<span class=c>no cleanup needed</span>';
  const gTxt=t.g!=null?esc(t.g):"(no reading — LightOn has a word Gemini doesn't)";
  const ens="v" in t
    ? `<div class=note>Engines disagreed: LightOn <b>${{esc(t.t)}}</b> vs Gemini <b>${{gTxt}}</b> → the vision model read <b>${{esc(t.v)}}</b></div>`
    : "g" in t
    ? `<div class=note style="color:var(--bad)">Engines disagree (unresolved): LightOn <b>${{esc(t.t)}}</b>, Gemini <b>${{gTxt}}</b></div>`
    : "";
  const evd=t.e
    ? `<div class=note style="color:var(--ok)">corrected from <b>${{esc(t.e.o)}}</b> · ${{esc(t.e.x)}} detector, `
      +`decided by ${{esc(t.e.d)}} (${{esc(t.e.r)}}) · ${{esc(t.e.at)}}</div>`
    : "";
  const box=P[cur].boxed?` · box [${{t.b.map(v=>v.toFixed(1)).join(", ")}}]`:"";
  insp.innerHTML=`<div class=row1><span class=word>${{esc(t.t)}}</span><span class=chips>${{src}}${{when}}${{fx}}</span></div>`
    +ens+evd+`<div class=tech>${{t.i}} · raw OCR “${{esc(t.r)}}”${{box}}</div>`;
}}
function placeMarker(i){{
  const mk=$("#marker"); if(!mk) return;
  const ws=txt.querySelectorAll(".w");
  if(i<0||!ws[i]){{ mk.classList.remove("on"); if(bmark) bmark.classList.remove("on"); return; }}
  const w=ws[i], r=w.getBoundingClientRect(), c=txt.getBoundingClientRect();
  mk.style.left=(r.left-c.left-4)+"px"; mk.style.top=(r.top-c.top-2)+"px";
  mk.style.width=(r.width+8)+"px"; mk.style.height=(r.height+4)+"px"; mk.classList.add("on");
  const p=P[cur];
  if(bmark&&p.boxed&&p.tokens[i].b){{ const b=p.tokens[i].b;
    Object.assign(bmark.style,{{left:b[0]+"%",top:b[1]+"%",width:b[2]+"%",height:b[3]+"%"}}); bmark.classList.add("on"); }}
  // keep the spoken word in view inside its column (or the page)
  const hr=txtcol?txtcol.getBoundingClientRect():{{top:70,bottom:innerHeight-110}};
  if(r.top<hr.top+20||r.bottom>hr.bottom-20) w.scrollIntoView({{block:"center",behavior:RM?"auto":"smooth"}});
}}
function render(){{
  const p=P[cur];
  if(!p.dur){{
    document.querySelectorAll(".w.now,.bx.now,.w.read").forEach(e=>e.classList.remove("now","read"));
    placeMarker(-1); bar.style.width="0%"; tm.textContent="— / —"; last=-1;
    return;
  }}
  const ms=au.currentTime*1000, ts=p.tokens;
  let i=-1;
  for(let k=0;k<ts.length;k++) if(ts[k].m&&ms>=ts[k].m[0]&&ms<=ts[k].m[1]){{i=k;break;}}
  if(i!==last){{
    const ws=txt.querySelectorAll(".w");
    ws.forEach((e,k)=>{{e.classList.toggle("now",k===i); e.classList.toggle("read",!!(ts[k].m&&ts[k].m[1]<ms)&&k!==i);}});
    if(p.boxed) document.querySelectorAll(".bx").forEach((e,k)=>e.classList.toggle("now",k===i));
    placeMarker(i);
    if(i>=0) show(i);
    last=i;
  }}
  const d=p.dur;
  bar.style.width=Math.min(100,100*ms/d)+"%";
  tm.textContent=(ms/1000).toFixed(2)+" / "+(d/1000).toFixed(2);
}}
function loop(){{ render(); if(!au.paused) raf=requestAnimationFrame(loop); }}
au.onplay=()=>{{document.body.classList.add("playing"); cancelAnimationFrame(raf); loop();}};
au.onpause=()=>{{document.body.classList.remove("playing"); render();}};
au.ontimeupdate=()=>{{ if(au.paused) render(); }};
au.onended=()=>{{
  document.body.classList.remove("playing"); seen.add(cur); renderPages();
  if(auto&&cur<P.length-1) setTimeout(()=>load(cur+1,true),450);
}};
playB.onclick=()=>{{if(!P[cur].audio)return; if(au.paused) au.play().catch(()=>{{}}); else au.pause();}};
$("#prev").onclick=()=>load(cur-1,!au.paused); $("#next").onclick=()=>load(cur+1,!au.paused);
$("#scrub").onclick=e=>{{if(!P[cur].dur)return; const r=e.currentTarget.getBoundingClientRect();
  au.currentTime=P[cur].dur/1000*Math.min(1,Math.max(0,(r.right-e.clientX)/r.width)); last=-1; render();}};
const speedB=$("#speed"), autoB=$("#auto");
const showSpeed=()=>{{speedB.textContent=speed+"×"; au.playbackRate=speed;}};
speedB.onclick=()=>{{speed=SPEEDS[(SPEEDS.indexOf(speed)+1)%SPEEDS.length]; store.set("reader-speed",speed); showSpeed();}};
autoB.classList.toggle("on",auto);
autoB.onclick=()=>{{auto=!auto; store.set("reader-auto",auto?"1":"0"); autoB.classList.toggle("on",auto);}};
$("#theme").onclick=()=>{{const d=document.documentElement, t=d.dataset.theme==="dark"?"light":"dark"; d.dataset.theme=t; store.set("reader-theme",t);}};
document.addEventListener("keydown",e=>{{
  if(e.target.closest("input,textarea,select")) return;
  if(e.code==="Space"){{e.preventDefault(); playB.click();}}
  else if(e.key==="ArrowLeft"){{e.preventDefault(); load(cur+1,!au.paused);}}   // RTL: left = next page
  else if(e.key==="ArrowRight"){{e.preventDefault(); load(cur-1,!au.paused);}}
}});
addEventListener("resize",()=>placeMarker(last));
// count-up stats and fade-in sections when they scroll into view
function countUp(el){{ const n=+el.dataset.count; if(RM||!n){{return;}} const t0=performance.now(), D=900;
  const f=now=>{{const k=Math.min(1,(now-t0)/D), e=1-Math.pow(1-k,3); el.textContent=Math.round(n*e); if(k<1) requestAnimationFrame(f);}};
  el.textContent="0"; requestAnimationFrame(f); }}
const io="IntersectionObserver" in window ? new IntersectionObserver(es=>es.forEach(en=>{{ if(!en.isIntersecting) return;
  en.target.classList.add("shown"); en.target.querySelectorAll("[data-count]").forEach(countUp); io.unobserve(en.target); }}),{{threshold:.15}}) : null;
document.querySelectorAll(".reveal").forEach(el=>io?io.observe(el):el.classList.add("shown"));
showSpeed();
load(0);
</script></body></html>"""
    out = work_dir / "reader.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB)")
    print(f"{len(pages)} pages · {n_tok} tokens · {n_anc} anchored · {n_fix} cleaned · {dur/60:.1f} min")
    if skipped:
        print(f"    skipped {len(skipped)} incomplete page(s):")
        for pg, st, why in skipped:
            print(f"      p{pg:03d}  [{st}]  {why}")
    return out


def main():
    book = os.environ.get("BOOK", "النجوم")
    build(Path(f"work/{book}"))


if __name__ == "__main__":
    main()
