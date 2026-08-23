"""Offline step 5: a side-by-side original-vs-corrected review page for a
small page range of one already-corrected LightOn book.

Standalone -- reuses ensemble_review.json's per-page alignment (which
already records EVERY token, not just disagreements) as the single source
of truth for both sequences:

  original  = each entry's lighton_txt, in order, skipping entries LightOn
              never produced at all (gemini-only inserts)
  corrected = whatever's in the CURRENT pXXX.json today (post
              apply_corrections.py substitutions/deletions, or post
              replace_page.py wholesale swap) looked up by id -- the real,
              already-shipped pipeline output, not a re-derivation

The corrected audio/timing is REUSED directly from the normal pipeline run
(already-synthesised audio/pXXX.wav + each token's own "t"/"t_src") rather
than re-synthesised -- it's already a full, independent track from
tonight's earlier work, so redoing it would just burn a TTS call to
reproduce audio that already exists. Only the ORIGINAL-OCR track is new:
one Gemini TTS + Whisper-alignment pass per page, written to
review_audio/, never touching audio/pXXX.wav or pXXX.json.

Usage:
    python3 tools/build_review.py "work/<book>-lighton" --pages 15:19
"""
import argparse
import json
import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv
load_dotenv()
from google import genai

from pipeline.tts_gemini import synthesize, VOICE, MODEL, SAMPLE_RATE
from pipeline.align_whisper import align_words
from pipeline.speak import bind
from pipeline.build_reader import _borrow_boxes  # noqa: E402


def build_pairs(review_page: list[dict], rec: dict, wholesale_replaced: bool) -> list[dict]:
    """One entry per logical word slot, correctly ordered.

    A wholesale-replaced page (replace_page.py) has entirely different ids
    now (Gemini's, not LightOn's) -- per-token original/corrected pairing
    isn't meaningful there, so it just walks the current tokens as-is, no
    strikethrough pairs. The original-OCR AUDIO track (built separately,
    from review_page's lighton_txt values) still carries the contrast.

    Otherwise, review_page's own order IS the true original reading order
    (it comes from aligning against the page before any correction ran),
    so walking it directly -- rather than walking today's already-edited
    token list and patching deletions onto the end -- keeps a token
    apply_corrections.py deleted in its real position instead of shunting
    it to the end of the page.
    """
    if wholesale_replaced:
        return [{"orig_txt": None, "corr_txt": t["txt"], "corr_tok": t, "status": "keep"}
                for t in rec["tokens"]]

    corrected_by_id = {t["id"]: t for t in rec["tokens"]}
    pairs = []
    for r in review_page:
        if r["id"].startswith("gemini-only:"):
            continue  # never applied to this page (none of these pages had any)
        corr_tok = corrected_by_id.get(r["id"])  # None if apply_corrections.py deleted it
        pairs.append({
            "orig_txt": r["lighton_txt"],
            "corr_txt": corr_tok["txt"] if corr_tok else None,
            "corr_tok": corr_tok,
            "status": r["action"].lower(),
        })
    return pairs


def synthesize_original(client, voice, original_words: list[str], pairs: list[dict], wav_path: Path):
    """original_words is sourced from ensemble_review.json directly (always
    correct, even on a wholesale-replaced page where pairs carry no
    orig_txt at all -- see build_pairs). Per-word timing is only written
    back onto pairs where a matching orig_txt position exists to attach it
    to; a wholesale-replaced page still gets its original-OCR audio, it
    just can't highlight individual words while it plays."""
    if not original_words:
        return 0

    # resumable: this model's free-tier quota is 10 requests/day -- a wav
    # already on disk from an earlier attempt is reused rather than
    # re-spending quota synthesising identical text again
    if wav_path.exists():
        with wave.open(str(wav_path), "rb") as wf:
            duration_ms = wf.getnframes() / wf.getframerate() * 1000
    else:
        text = " ".join(original_words)
        pcm, duration_ms = synthesize(client, text, voice)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)

    temp_tokens = [{"txt": w} for w in original_words]
    hume_words = align_words(wav_path)
    temp_tokens, _ = bind(temp_tokens, hume_words, int(duration_ms))

    ti = 0
    for p in pairs:
        if p["orig_txt"]:
            p["orig_t"] = temp_tokens[ti].get("t")
            ti += 1
    return int(duration_ms)


def build_page(client, voice, work_dir: Path, gemini_dir: Path, review: dict, page_no: int) -> dict | None:
    page_path = work_dir / f"p{page_no:03d}.json"
    if not page_path.exists():
        print(f"page {page_no}: no such page, skipping")
        return None
    rec = json.loads(page_path.read_text(encoding="utf-8"))
    if rec.get("status") != "spoken":
        print(f"page {page_no}: status={rec.get('status')!r}, not spoken yet -- skipping")
        return None

    review_page = review["pages"].get(str(page_no), [])
    if not review_page:
        # Gemini's OCR run never reached this page at all (no comparison
        # data exists, regardless of whether a page image happens to be on
        # disk from a partial render) -- nothing to review, so this page is
        # excluded rather than shown with a stray image and no content
        print(f"page {page_no}: no Gemini comparison data for this page, skipping")
        return None
    wholesale_replaced = "ocr_engine_replaced_from" in rec
    pairs = build_pairs(review_page, rec, wholesale_replaced)

    # image + boxes: native if this page has its own (e.g. replace_page.py's
    # wholesale swap), otherwise borrow via the same alignment build_reader.py uses.
    # LightOn's OCR path never saves page images at all, so even a page with
    # native boxes (a wholesale swap copies Gemini's box coordinates, not the
    # image file) falls back to Gemini's rendered page -- same fix as build_reader.py.
    has_native_boxes = rec.get("box_level", "word") not in (None, "none")
    own_jpg = work_dir / "pages" / f"p{page_no:03d}.jpg"
    gemini_jpg = gemini_dir / "pages" / f"p{page_no:03d}.jpg"
    if has_native_boxes:
        img = os.path.relpath(own_jpg, start=work_dir) if own_jpg.exists() \
            else (os.path.relpath(gemini_jpg, start=work_dir) if gemini_jpg.exists() else None)
        boxes_by_id = {t["id"]: t["box"] for t in rec["tokens"] if "box" in t}
    else:
        boxes_by_id = _borrow_boxes(rec["tokens"], gemini_dir, page_no)
        img = os.path.relpath(gemini_jpg, start=work_dir) if gemini_jpg.exists() else None

    for p in pairs:
        ct = p.pop("corr_tok")
        p["corr_t"] = ct.get("t") if ct else None
        p["box"] = boxes_by_id.get(ct["id"]) if ct else None

    original_words = [r["lighton_txt"] for r in review_page if r["lighton_txt"]]
    review_audio_dir = work_dir / "review_audio"
    wav_path = review_audio_dir / f"p{page_no:03d}_original.wav"
    orig_dur = synthesize_original(client, voice, original_words, pairs, wav_path)
    if wholesale_replaced:
        summary = (f"whole page replaced from Gemini -- LightOn had only "
                   f"{len(original_words)}/{len(pairs)} of these words")
    else:
        summary = f"{sum(1 for p in pairs if p['status']=='verified')}/{len(pairs)} words corrected"
    print(f"page {page_no}: {summary}, original audio {orig_dur/1000:.1f}s")

    return {
        "page": page_no,
        "img": img,
        "original_audio": os.path.relpath(wav_path, start=work_dir) if orig_dur else None,
        "original_dur": orig_dur,
        "corrected_audio": rec["audio"],
        "corrected_dur": rec["duration_ms"],
        "pairs": pairs,
    }


HTML_TEMPLATE = """<!doctype html><html lang=ar dir=rtl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{book} — مراجعة الصفحات {start}-{end}</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
color:#1a1d21;background:#eef1f4;padding:26px 18px}}
.pg{{max-width:1500px;margin:0 auto}}
.hd{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:10px;margin-bottom:16px}}
h1{{font-size:24px;font-weight:700}}
.sub{{font-size:12.5px;color:#7a8290;direction:ltr}}
.card{{background:#fff;border:1px solid #dfe3e8;border-radius:14px;overflow:hidden;margin-bottom:16px}}
.pgsel{{display:flex;gap:4px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid #eef1f4;direction:ltr}}
.pgsel button{{font:inherit;font-size:11.5px;width:30px;height:28px;border:1px solid #dfe3e8;
background:#fff;border-radius:6px;cursor:pointer;color:#5c646e}}
.pgsel button.on{{background:#1c4f8f;border-color:#1c4f8f;color:#fff;font-weight:700}}
.fontctl{{display:flex;gap:6px;align-items:center;padding:10px 14px;border-bottom:1px solid #eef1f4;direction:ltr;font-size:12px;color:#5c646e}}
.fontctl button{{font:inherit;width:26px;height:26px;border:1px solid #dfe3e8;background:#fff;border-radius:6px;cursor:pointer}}
.layout{{display:flex;align-items:stretch}}
.layout>div{{flex:1;min-width:0}}
.txtcol{{max-height:80vh;overflow:auto;border-left:1px solid #eef1f4;padding:24px 28px}}
.imgcol{{max-height:80vh;overflow:auto;background:#000}}
@media (max-width:900px){{.layout{{flex-direction:column}} .txtcol{{border-left:none;border-bottom:1px solid #eef1f4}}}}
.txt{{font-size:24px;line-height:2.7;font-family:'Amiri','Scheherazade New','Noto Naskh Arabic','Geeza Pro',serif}}
.pair{{padding:2px 3px}}
.orig{{color:#a4392f;text-decoration:line-through;text-decoration-color:#c98079;opacity:.75;cursor:pointer;border-radius:4px}}
.corr{{color:#0f6b3a;font-weight:700;cursor:pointer;border-radius:4px}}
.plain{{cursor:pointer;border-radius:4px}}
.deleted{{color:#a4392f;text-decoration:line-through;opacity:.6}}
.now{{background:#ffe9a8;box-shadow:0 0 0 2px #e3b778}}
.imgw{{position:relative;line-height:0}}
.imgw img{{width:100%;display:block}}
.bx{{position:absolute;border:2px solid transparent;border-radius:3px}}
.bx.verified{{border-color:#c98a2e;background:#f3c96a22}}
.bx.flag{{border-color:#c0392b;background:#e0776822}}
.bx.now{{border-color:#1c4f8f;background:#1c4f8f33}}
.aud{{padding:12px 16px;border-top:1px solid #eef1f4}}
.aud .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#7a8290;margin-bottom:6px;direction:ltr}}
.aud .row{{display:flex;gap:9px;align-items:center;direction:ltr}}
.aud button{{font:inherit;font-size:12.5px;padding:6px 11px;border:1px solid #dfe3e8;background:#fff;border-radius:8px;cursor:pointer}}
.scrub{{flex:1;height:6px;background:#e6eaee;border-radius:3px;position:relative;cursor:pointer}}
.scrub i{{position:absolute;inset:0 auto 0 0;width:0;background:#1c4f8f;border-radius:3px;display:block}}
.tm{{font-variant-numeric:tabular-nums;font-size:11.5px;color:#7a8290;min-width:80px;text-align:right}}
.lg{{display:flex;gap:15px;flex-wrap:wrap;font-size:11.5px;color:#5c646e;padding:12px 16px;border-top:1px solid #eef1f4;direction:ltr}}
.lg span{{display:flex;align-items:center;gap:5px}}
.lg i{{display:inline-block;width:11px;height:11px;border-radius:3px}}
</style></head><body><div class=pg>
<div class=hd><h1>{book} — مراجعة الصفحات {start}-{end}</h1>
<div class=sub>original OCR vs. VLM-corrected — LightOn vs Gemini, boxes color-coded by status</div></div>
<div class=card>
<div class=pgsel id=pgsel></div>
<div class=fontctl>font size <button id=fdown>−</button><span id=fsize>24px</span><button id=fup>+</button></div>
<div class=layout id=layout>
  <div class=txtcol><div class=txt id=txt></div></div>
  <div class=imgcol><div class=imgw id=imgw><img id=img alt=""><div id=boxes></div></div></div>
</div>
<div class=aud>
  <div class=lbl>original OCR audio (LightOn, as actually recognised)</div>
  <div class=row><button id=playO>▶</button><div class=scrub id=scrubO><i id=barO></i></div><div class=tm id=tmO>0.00 / 0.00</div></div>
</div>
<div class=aud>
  <div class=lbl>corrected audio (VLM-verified)</div>
  <div class=row><button id=playC>▶</button><div class=scrub id=scrubC><i id=barC></i></div><div class=tm id=tmC>0.00 / 0.00</div></div>
</div>
<div class=lg>
<span><i style="background:#f3c96a55;border:1px solid #c98a2e"></i>VLM-corrected word</span>
<span><i style="background:#fff;border:2px solid #a4392f;opacity:.6"></i>strikethrough = original OCR word</span>
</div>
</div>
<audio id=auO preload=metadata></audio>
<audio id=auC preload=metadata></audio>
<script>
const P={data};
let cur=0;
const $=s=>document.querySelector(s);
const txt=$("#txt"),imgw=$("#imgw"),img=$("#img"),boxes=$("#boxes"),
      auO=$("#auO"),auC=$("#auC"),barO=$("#barO"),barC=$("#barC"),tmO=$("#tmO"),tmC=$("#tmC"),
      playO=$("#playO"),playC=$("#playC");
const esc=s=>(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;");

$("#pgsel").innerHTML=P.map((p,i)=>`<button data-p="${{i}}"${{i?"":" class=on"}}>${{p.page}}</button>`).join("");
$("#pgsel").onclick=e=>{{const b=e.target.closest("button"); if(b) load(+b.dataset.p);}};
$("#fup").onclick=()=>setFont(1); $("#fdown").onclick=()=>setFont(-1);
function setFont(delta){{
  const cur=parseInt(getComputedStyle(txt).fontSize);
  const next=Math.max(14,Math.min(40,cur+delta*2));
  txt.style.fontSize=next+"px"; $("#fsize").textContent=next+"px";
}}

function load(i){{
  cur=i; const p=P[i];
  auO.src=p.original_audio||""; auC.src=p.corrected_audio;
  txt.innerHTML=""; img.src=p.img||""; boxes.innerHTML="";
  imgw.style.display=p.img?"":"none";
  p.pairs.forEach((pr,j)=>{{
    if(pr.orig_txt&&pr.corr_txt&&pr.orig_txt!==pr.corr_txt){{
      const wrap=document.createElement("span"); wrap.className="pair";
      const o=document.createElement("span"); o.className="orig w-orig"; o.dataset.j=j; o.textContent=pr.orig_txt;
      const c=document.createElement("span"); c.className="corr w-corr"; c.dataset.j=j; c.textContent=pr.corr_txt;
      wrap.append(o,document.createTextNode(" "),c);
      txt.appendChild(wrap);
    }} else if(pr.corr_txt){{
      const s=document.createElement("span"); s.className="plain w-orig w-corr"; s.dataset.j=j; s.textContent=pr.corr_txt;
      txt.appendChild(s);
    }} else if(pr.orig_txt){{
      const s=document.createElement("span"); s.className="deleted w-orig"; s.dataset.j=j; s.textContent=pr.orig_txt;
      txt.appendChild(s);
    }}
    txt.appendChild(document.createTextNode(" "));
    if(pr.box){{
      const b=document.createElement("div");
      b.className="bx "+(pr.status==="verified"?"verified":pr.status==="flag"?"flag":"");
      b.dataset.j=j;
      b.style.cssText=`left:${{pr.box[0]}}%;top:${{pr.box[1]}}%;width:${{pr.box[2]}}%;height:${{pr.box[3]}}%`;
      boxes.appendChild(b);
    }}
  }});
  document.querySelectorAll("#pgsel button").forEach((b,k)=>b.classList.toggle("on",k===i));
  renderO(); renderC();
}}
function highlight(cls,j){{
  document.querySelectorAll("."+cls).forEach(e=>e.classList.toggle("now",+e.dataset.j===j));
  document.querySelectorAll(".bx").forEach(e=>e.classList.toggle("now",+e.dataset.j===j));
}}
function renderO(){{
  const ms=auO.currentTime*1000, ps=P[cur].pairs;
  let j=-1;
  for(let k=0;k<ps.length;k++) if(ps[k].orig_t&&ms>=ps[k].orig_t[0]&&ms<=ps[k].orig_t[1]){{j=k;break;}}
  if(j>=0) highlight("w-orig",j);
  const d=P[cur].original_dur||1;
  barO.style.width=Math.min(100,100*ms/d)+"%";
  tmO.textContent=(ms/1000).toFixed(2)+" / "+(d/1000).toFixed(2);
}}
function renderC(){{
  const ms=auC.currentTime*1000, ps=P[cur].pairs;
  let j=-1;
  for(let k=0;k<ps.length;k++) if(ps[k].corr_t&&ms>=ps[k].corr_t[0]&&ms<=ps[k].corr_t[1]){{j=k;break;}}
  if(j>=0) highlight("w-corr",j);
  const d=P[cur].corrected_dur||1;
  barC.style.width=Math.min(100,100*ms/d)+"%";
  tmC.textContent=(ms/1000).toFixed(2)+" / "+(d/1000).toFixed(2);
}}
auO.ontimeupdate=renderO; auC.ontimeupdate=renderC;
auO.onended=()=>{{playO.textContent="▶";}}; auC.onended=()=>{{playC.textContent="▶";}};
playO.onclick=()=>{{if(auO.paused){{auO.play();playO.textContent="⏸";}}else{{auO.pause();playO.textContent="▶";}}}};
playC.onclick=()=>{{if(auC.paused){{auC.play();playC.textContent="⏸";}}else{{auC.pause();playC.textContent="▶";}}}};
$("#scrubO").onclick=e=>{{const r=e.currentTarget.getBoundingClientRect(); auO.currentTime=(P[cur].original_dur||0)/1000*(e.clientX-r.left)/r.width; renderO();}};
$("#scrubC").onclick=e=>{{const r=e.currentTarget.getBoundingClientRect(); auC.currentTime=(P[cur].corrected_dur||0)/1000*(e.clientX-r.left)/r.width; renderC();}};
load(0);
</script></body></html>"""


def run(work_dir: Path, start: int, end: int, voice: str = VOICE) -> Path:
    review_path = work_dir / "ensemble_review.json"
    if not review_path.exists():
        raise SystemExit(f"no ensemble_review.json in {work_dir}")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    gemini_dir = Path(review["gemini_dir"])

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    pages_out = []
    for page_no in range(start, end + 1):
        page = build_page(client, voice, work_dir, gemini_dir, review, page_no)
        if page:
            pages_out.append(page)

    if not pages_out:
        raise SystemExit("no pages built -- nothing to write")

    html = HTML_TEMPLATE.format(
        book=work_dir.name, start=start, end=end,
        data=json.dumps(pages_out, ensure_ascii=False, separators=(",", ":")))
    out = work_dir / f"review_{start}-{end}.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nwrote {out} ({out.stat().st_size/1024:.0f} KB)")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Build a side-by-side original-vs-corrected review page for a page range.")
    ap.add_argument("work_dir", help="work/<book>-lighton directory")
    ap.add_argument("--pages", default="15:19", help="'start:end' inclusive, e.g. 15:19")
    ap.add_argument("--voice", default=VOICE)
    args = ap.parse_args()

    start, end = (int(x) for x in args.pages.split(":"))
    work_dir = Path(args.work_dir)
    if not work_dir.is_dir():
        sys.exit(f"no such directory: {work_dir}")
    run(work_dir, start, end, args.voice)


if __name__ == "__main__":
    main()
