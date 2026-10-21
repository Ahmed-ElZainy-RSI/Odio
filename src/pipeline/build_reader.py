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

Whether a given page ends up with a compare view (native OR borrowed boxes,
plus an image) is therefore a PER-PAGE decision, carried in each page's
"compare" flag -- not a single book-wide switch. The two-column DOM is
rendered once if ANY page qualifies; load() hides the image column for
pages that don't.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from ensemble_review import align_page  # noqa: E402


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
        # A page is rendered only when all three artefacts genuinely exist:
        # this JSON record, a synthesised audio file on disk, and a page image.
        # Anything less is skipped and reported, never shown half-working.
        # (The image is resolved further down -- it may be borrowed -- so the
        # completeness decision is made just before the page is appended.)
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
        if not audio_rel or not (work_dir / audio_rel).exists():
            why.append("no audio file")
        elif not r.get("duration_ms"):
            why.append("audio has no duration")
        if not has_image:
            why.append("no page image")
        if why:
            skipped.append((r["page"], r.get("status", "?"), " + ".join(why)))
            continue

        pages.append({"page": r["page"], "audio": audio_rel,
            "img": img_path, "dur": r["duration_ms"],
            "diac": r["diac_ratio"], "box_level": r.get("box_level", "word"),
            "has_image": has_image, "compare": has_boxes and has_image,
            "voice": r.get("voice", ""), "tokens": tok})

    if not pages:
        total = len(list(work_dir.glob("p*.json")))
        print(f"{work_dir.name}: 0 of {total} pages are complete (need JSON + audio file + page image) "
              f"— nothing to build.")
        for pg, st, why in skipped:
            print(f"    p{pg:03d}  [{st}]  {why}")
        return None

    # per-page "compare" already decides native vs. borrowed vs. neither;
    # the DOM only needs the two-column structure if at least one page uses it
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

    html = f"""<!doctype html><html lang=ar dir=rtl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{book} — قارئ متزامن</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
color:#1a1d21;background:#eef1f4;padding:26px 18px}}
.pg{{max-width:{'1400px' if has_compare else '1000px'};margin:0 auto}}
.hd{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:10px;margin-bottom:16px}}
h1{{font-size:26px;font-weight:700}}
.sub{{font-size:12.5px;color:#7a8290;direction:ltr}}
.card{{background:#fff;border:1px solid #dfe3e8;border-radius:14px;overflow:hidden}}
.bar{{display:flex;gap:9px;align-items:center;padding:11px 14px;background:#fafbfc;
border-bottom:1px solid #eef1f4;flex-wrap:wrap;direction:ltr}}
.bar button{{font:inherit;font-size:12.5px;padding:7px 13px;border:1px solid #dfe3e8;
background:#fff;border-radius:8px;cursor:pointer;color:#3c434c}}
.bar button:hover{{border-color:#b9c2cc}}
.bar button.on{{background:#14233a;border-color:#14233a;color:#fff}}
.bar button:disabled{{opacity:.35;cursor:default}}
.scrub{{flex:1;min-width:110px;height:6px;background:#e6eaee;border-radius:3px;position:relative;cursor:pointer}}
.scrub i{{position:absolute;inset:0 auto 0 0;width:0;background:#1c4f8f;border-radius:3px;display:block}}
.tm{{font-variant-numeric:tabular-nums;font-size:12px;color:#7a8290;min-width:86px;text-align:right}}
.pgsel{{display:flex;gap:4px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid #eef1f4;direction:ltr}}
.pgsel button{{font:inherit;font-size:11.5px;width:30px;height:28px;border:1px solid #dfe3e8;
background:#fff;border-radius:6px;cursor:pointer;color:#5c646e}}
.pgsel button.on{{background:#1c4f8f;border-color:#1c4f8f;color:#fff;font-weight:700}}

.compare{{display:flex;align-items:stretch}}
.compare>div{{flex:1;min-width:0}}
.txtcol{{max-height:75vh;overflow:auto;border-left:1px solid #eef1f4}}
.imgcol{{max-height:75vh;overflow:auto;background:#000}}
.compare.textonly .imgcol{{display:none}}
.compare.textonly .txtcol{{border-left:none}}
@media (max-width:760px){{.compare{{flex-direction:column}} .txtcol{{border-left:none;border-bottom:1px solid #eef1f4}}}}
.txt{{padding:26px 30px;font-size:24px;line-height:2.6;
font-family:'Amiri','Scheherazade New','Noto Naskh Arabic','Geeza Pro',serif;min-height:220px}}
.w{{padding:2px 4px;border-radius:5px;cursor:pointer}}
.w:hover{{background:#eef4fc;outline:1px solid #c3d5ee}}
.w.now{{background:#ffe9a8;box-shadow:0 0 0 2px #e3b778}}
.w.interp{{border-bottom:2px dotted #c3cbd4}}
.w.fixed{{background:#f4fbf6}}
.w.fixed.now{{background:#ffe9a8}}
.w.flagged{{background:#fdeceb;outline:1px solid #e8b4ae}}
.w.flagged.now{{background:#ffe9a8}}
.w.verified{{background:#fdf3e0;outline:1px solid #e8c98a}}
.w.verified.now{{background:#ffe9a8}}
.w.untimed{{opacity:.55;cursor:default}}
.imgw{{position:relative;line-height:0}}
.imgw img{{width:100%;display:block}}
.bx{{position:absolute;border:2px solid transparent;border-radius:3px;cursor:pointer}}
.bx:hover{{border-color:#1c4f8f;background:#1c4f8f22}}
.bx.now{{border-color:#e3a11a;background:#ffd97e55}}
.insp{{padding:12px 16px;border-top:1px solid #eef1f4;background:#fafbfc;direction:ltr;
font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#5c646e;min-height:58px}}
.insp b{{color:#1a1d21}}
.lg{{display:flex;gap:15px;flex-wrap:wrap;font-size:11.5px;color:#5c646e;margin-top:12px;direction:ltr}}
.lg i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.stat{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:16px;direction:ltr}}
.stat div{{background:#fff;border:1px solid #dfe3e8;border-radius:10px;padding:11px 13px}}
.stat b{{display:block;font-size:19px;font-variant-numeric:tabular-nums}}
.stat span{{font-size:10.5px;color:#7a8290}}
</style></head><body><div class=pg>

<div class=hd><h1>{book}</h1>
<div class=sub>{len(pages)} pages · {n_tok} tokens · {dur/60:.1f} min{f" · {voice}" if voice else ""}{f" · page image available for {n_compare}/{len(pages)} pages" if has_compare and n_compare < len(pages) else ("" if has_compare else " · text view only — no page image available")}</div></div>

<div class=card>
<div class=pgsel id=pgsel></div>
<div class=bar>
<button id=play>▶</button>
<button id=prev>‹</button><button id=next>›</button>
<div class=scrub id=scrub><i id=bar></i></div>
<div class=tm id=tm>0.00 / 0.00</div>
</div>
{'<div class=compare id=compare><div class=txtcol><div class=txt id=txt></div></div><div class=imgcol><div class=imgw id=imgw><img id=img alt=""><div id=boxes></div></div></div></div>' if has_compare else '<div class=txt id=txt></div>'}
<div class=insp id=insp>Hover a word to inspect its token record.</div>
</div>

<div class=lg>
<span><i style="background:#ffe9a8"></i>currently spoken</span>
<span><i style="background:#f4fbf6;border:1px solid #cfe8d8"></i>cleaned by a rule</span>
<span><i style="border-bottom:2px dotted #c3cbd4;width:14px;height:8px"></i>timing interpolated</span>
{'<span><i style="background:#fdeceb;border:1px solid #e8b4ae"></i>disagrees with a second engine, unresolved</span>' if n_flag else ''}
{'<span><i style="background:#fdf3e0;border:1px solid #e8c98a"></i>disagreement, VLM-verified reading available</span>' if n_verified else ''}
</div>
<div class=stat>
<div><b>{n_anc}</b><span>anchored to real timestamps{f" ({100*n_anc/n_timed:.0f}% of timed)" if n_timed else ""}</span></div>
<div><b>{n_interp}</b><span>interpolated between anchors</span></div>
<div><b>{n_fix}</b><span>tokens cleaned by an extraction rule</span></div>
{f'<div><b>{n_flag}</b><span>unresolved disagreements ({100*n_flag/n_tok:.0f}%)</span></div>' if n_flag else ''}
{f'<div><b>{n_verified}</b><span>VLM-verified disagreements ({100*n_verified/n_tok:.0f}%)</span></div>' if n_verified else ''}
<div><b>{dur/60:.1f} min</b><span>real synthesised audio</span></div>
</div>
</div>
<audio id=au preload=metadata></audio>
<script>
const P={data};
const HAS_COMPARE_LAYOUT={str(has_compare).lower()};
let cur=0;
const $=s=>document.querySelector(s);
const au=$("#au"),txt=$("#txt"),compare=$("#compare"),imgw=$("#imgw"),img=$("#img"),boxes=$("#boxes"),
      insp=$("#insp"),bar=$("#bar"),tm=$("#tm"),playB=$("#play");
const esc=s=>s.replace(/&/g,"&amp;").replace(/</g,"&lt;");

$("#pgsel").innerHTML=P.map((p,i)=>`<button data-p="${{i}}"${{i?"":" class=on"}} title="page ${{p.page}}">${{p.page}}</button>`).join("");
$("#pgsel").onclick=e=>{{const b=e.target.closest("button"); if(b) load(+b.dataset.p);}};

function load(i){{
  cur=i; const p=P[i];
  // A page without audio must not set au.src -- an empty/missing src fires a
  // network error and leaves the previous page's audio loaded and playable.
  au.pause();
  if(p.audio){{ au.src=p.audio; }} else {{ au.removeAttribute("src"); au.load(); }}
  playB.disabled=!p.audio; playB.textContent="\u25b6";
  $("#scrub").style.opacity=p.audio?"":"0.35";
  $("#scrub").style.cursor=p.audio?"":"default";
  txt.innerHTML="";
  if(HAS_COMPARE_LAYOUT){{
    compare.classList.toggle("textonly",!p.compare);
    if(p.compare){{ img.src=p.img; boxes.innerHTML=""; }}
  }}
  p.tokens.forEach((t,j)=>{{
    const s=document.createElement("span");
    s.className="w"+(t.s==="interpolated"?" interp":"")+(t.f.length?" fixed":"")
      +("g" in t&&!("v" in t)?" flagged":"")+("v" in t?" verified":"")+(t.m?"":" untimed");
    s.dataset.j=j; s.textContent=t.t;
    txt.appendChild(s); txt.appendChild(document.createTextNode(" "));
    if(p.compare){{
      const b=document.createElement("div");
      b.className="bx"; b.dataset.j=j;
      b.style.cssText=`left:${{t.b[0]}}%;top:${{t.b[1]}}%;width:${{t.b[2]}}%;height:${{t.b[3]}}%`;
      boxes.appendChild(b);
    }}
  }});
  document.querySelectorAll("#pgsel button").forEach((b,k)=>b.classList.toggle("on",k===i));
  $("#prev").disabled=!i; $("#next").disabled=i===P.length-1;
  wire(); render();
}}
function wire(){{
  document.querySelectorAll(".w,.bx").forEach(el=>{{
    el.onmouseenter=()=>show(+el.dataset.j);
    el.onclick=()=>{{const m=P[cur].tokens[+el.dataset.j].m;
      if(m&&P[cur].audio){{au.currentTime=m[0]/1000; render();}}}};
  }});
}}
function show(j){{
  const t=P[cur].tokens[j];
  const src=t.s==="anchor"
    ? '<span style="color:#0f7b4a">anchor — real timestamp</span>'
    : t.s==="interpolated"
    ? '<span style="color:#8a8f96">interpolated between anchors</span>'
    : '<span style="color:#c0392b">no timing available</span>';
  const fx=t.f.length?`<span style="color:#1c4f8f">cleaned: ${{t.f.join(", ")}}</span>`
                     :'<span style="color:#a4abb4">no cleanup needed</span>';
  const box=P[cur].compare?` &nbsp;·&nbsp; box [${{t.b.map(v=>v.toFixed(1)).join(", ")}}]`:"";
  const timing=t.m?`t [${{t.m[0]}}, ${{t.m[1]}}] ms · `:"";
  const gTxt=t.g!=null?esc(t.g):"(no reading — LightOn has a word Gemini doesn't)";
  const ens="v" in t
    ? `<br><span style="color:#9a6b00">ensemble: LightOn <b>${{esc(t.t)}}</b> vs Gemini <b>${{gTxt}}</b> → VLM read <b>${{esc(t.v)}}</b></span>`
    : "g" in t
    ? `<br><span style="color:#c0392b">ensemble: LightOn says <b>${{esc(t.t)}}</b>, Gemini says <b>${{gTxt}}</b> (unresolved)</span>`
    : "";
  const evd=t.e
    ? `<br><span style="color:#0f6b3a">corrected from <b>${{esc(t.e.o)}}</b> · ${{esc(t.e.x)}} detector, `
      +`decided by ${{esc(t.e.d)}} (${{esc(t.e.r)}}) · ${{esc(t.e.at)}}</span>`
    : "";
  insp.innerHTML=`<b>${{t.i}}</b> &nbsp; raw <b>${{esc(t.r)}}</b> → <b>${{esc(t.t)}}</b><br>`
   +`${{timing}}${{src}} &nbsp;·&nbsp; ${{fx}}${{box}}${{ens}}${{evd}}`;
}}
let last=-1;
function render(){{
  const p=P[cur];
  if(!p.dur){{
    document.querySelectorAll(".w.now,.bx.now").forEach(e=>e.classList.remove("now"));
    bar.style.width="0%"; tm.textContent="— / —"; last=-1;
    return;
  }}
  const ms=au.currentTime*1000, ts=p.tokens;
  let i=-1;
  for(let k=0;k<ts.length;k++) if(ts[k].m&&ms>=ts[k].m[0]&&ms<=ts[k].m[1]){{i=k;break;}}
  document.querySelectorAll(".w").forEach((e,k)=>e.classList.toggle("now",k===i));
  if(p.compare) document.querySelectorAll(".bx").forEach((e,k)=>e.classList.toggle("now",k===i));
  if(i>=0&&i!==last){{show(i);last=i;}}
  const d=p.dur;
  bar.style.width=Math.min(100,100*ms/d)+"%";
  tm.textContent=(ms/1000).toFixed(2)+" / "+(d/1000).toFixed(2);
}}
au.ontimeupdate=render;
au.onended=()=>{{playB.textContent="▶";}};
playB.onclick=()=>{{if(!P[cur].audio)return;
  if(au.paused){{au.play();playB.textContent="⏸";}}else{{au.pause();playB.textContent="▶";}}}};
$("#prev").onclick=()=>load(cur-1); $("#next").onclick=()=>load(cur+1);
$("#scrub").onclick=e=>{{if(!P[cur].dur)return; const r=e.currentTarget.getBoundingClientRect();
  au.currentTime=P[cur].dur/1000*(e.clientX-r.left)/r.width; render();}};
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
