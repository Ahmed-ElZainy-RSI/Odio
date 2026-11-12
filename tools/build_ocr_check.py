"""Build a hand-checking page for one book's OCR: page image beside the OCR words.

The point is a ground-truth error rate for the OCR engine, letters AND diacritics, on
real books. Nothing in the pipeline measures that: ensemble_review.py strips diacritics
before comparing, and a page routed 'keep' is spoken with exactly the OCR's diacritics.

For every word the page shows Gemini's reading next to a zoomed crop of that word on a
high-resolution render of the PDF page, and the checker marks it: correct, wrong letters,
wrong diacritics, not on the page (extra), or a word missing after it, with the right
spelling. Unmarked words on a page marked "done" count as correct.

Marks are kept in the browser (localStorage) and exported as JSON with the Export button;
the export carries the per-word marks plus the summary the page shows.

Read-only for the pipeline: writes only <work_dir>/ocr_check.html and
<work_dir>/check_pages/pXXX.jpg (the high-resolution renders).

Usage:
    python3 tools/build_ocr_check.py "work/<book>"
    python3 tools/build_ocr_check.py "work/<book>" --pdf "assets/PDF Samples/<book>.pdf"
"""
import argparse
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
CHECK_EDGE = 2400  # long edge of the check render, px: enough to read a fatha on a crop


def find_pdf(work_dir: Path, records: list[dict]) -> Path:
    name = records[0].get("source_pdf")
    p = ROOT / "assets" / "PDF Samples" / name if name else None
    if p and p.exists():
        return p
    sys.exit(f"source PDF {name!r} not found under assets/PDF Samples -- pass --pdf")


def render_pages(pdf: Path, out_dir: Path, page_numbers: list[int]) -> None:
    out_dir.mkdir(exist_ok=True)
    doc = pymupdf.open(str(pdf))
    for n in page_numbers:
        out = out_dir / f"p{n:03d}.jpg"
        if out.exists():
            continue
        page = doc[n - 1]
        scale = CHECK_EDGE / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        out.write_bytes(pix.tobytes("jpeg", jpg_quality=90))
    doc.close()


def page_data(rec: dict) -> dict:
    return {"page": rec["page"], "path": rec.get("path", "?"), "diac": rec.get("diac_ratio"),
            "img": f"check_pages/p{rec['page']:03d}.jpg",
            "tokens": [{"i": t["id"], "t": t["txt"], "r": t["raw"], "b": t.get("box")}
                       for t in rec["tokens"]]}


def build(work_dir: Path, pdf: Path | None = None) -> Path:
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(work_dir.glob("p*.json"))]
    records = [r for r in records if r.get("tokens")]
    if not records:
        sys.exit(f"{work_dir}: no OCR pages with words")
    pdf = pdf or find_pdf(work_dir, records)
    render_pages(pdf, work_dir / "check_pages", [r["page"] for r in records])
    book = records[0].get("book", work_dir.name)
    engine = records[0].get("ocr_engine", "?")
    data = {"book": book, "engine": engine, "key": f"ocrcheck:{records[0].get('pdf_sha256_16', book)}:{engine}",
            "pages": [page_data(r) for r in records]}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__BOOK__", book).replace("__DATA__", payload)
    out = work_dir / "ocr_check.html"
    out.write_text(html, encoding="utf-8")
    n_words = sum(len(p["tokens"]) for p in data["pages"])
    print(f"wrote {out}  ({len(records)} pages, {n_words} words, OCR by {engine})")
    return out


TEMPLATE = r"""<!doctype html><html lang=ar dir=rtl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>OCR check — __BOOK__</title><style>
:root{--bg:#eef1f4;--card:#fff;--line:#dfe3e8;--soft:#fafbfc;--ink:#1a1d21;--mute:#6b7380;
--blue:#1c4f8f;--ok:#1f7a4d;--okbg:#e5f4ec;--let:#b3261e;--letbg:#fbe4e2;--dia:#9a5b00;--diabg:#fdf0d9;
--ext:#6a3fa0;--extbg:#efe7fa;--sel:#ffd97e}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1d2126;--line:#30363d;--soft:#181c20;
--ink:#e6e8eb;--mute:#97a0ab;--blue:#6ea8ff;--ok:#5fcf97;--okbg:#173527;--let:#ff8a80;--letbg:#3d1d1b;
--dia:#f2b45a;--diabg:#3a2b12;--ext:#c4a3f5;--extbg:#2c2340;--sel:#8a6a1c}}
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg);padding:18px 16px}
.wrap{max-width:1500px;margin:0 auto}
.ltr{direction:ltr}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-bottom:12px}
h1{font-size:22px}
.sub{color:var(--mute);font-size:12.5px;direction:ltr}
.btn{font:inherit;font-size:12.5px;padding:6px 12px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;cursor:pointer}
.btn:hover{border-color:var(--blue)}
.btn.pri{background:var(--blue);border-color:var(--blue);color:#fff}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:12px;direction:ltr}
.stats div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 11px}
.stats b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
.stats span{font-size:11px;color:var(--mute)}
.pgs{display:flex;gap:4px;flex-wrap:wrap;direction:ltr;margin-bottom:10px}
.pgs button{font:inherit;font-size:12px;min-width:34px;height:30px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;cursor:pointer;position:relative}
.pgs button.on{outline:2px solid var(--blue)}
.pgs button.done{background:var(--okbg);border-color:var(--ok)}
.pgs button.err::after{content:"";position:absolute;top:3px;right:3px;width:6px;height:6px;border-radius:3px;background:var(--let)}
.main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}
@media (max-width:900px){.main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.phd{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid var(--line);background:var(--soft);direction:ltr;flex-wrap:wrap}
.tag{font-size:11px;padding:2px 8px;border-radius:10px;background:var(--diabg);color:var(--dia)}
.tag.keep{background:var(--okbg);color:var(--ok)}
.txt{padding:16px 18px;font-family:'Amiri','Scheherazade New','Noto Naskh Arabic','Geeza Pro',serif;font-size:27px;line-height:2.2}
.w{cursor:pointer;padding:0 3px;border-radius:5px;border-bottom:2px solid transparent}
.w:hover{background:var(--soft)}
.w.ok{border-bottom-color:var(--ok)}
.w.let{background:var(--letbg);color:var(--let)}
.w.dia{background:var(--diabg);color:var(--dia)}
.w.ext{background:var(--extbg);color:var(--ext);text-decoration:line-through}
.w.sel{outline:3px solid var(--sel)}
.miss{display:inline-block;font-size:15px;padding:0 6px;border:1px dashed var(--ext);color:var(--ext);border-radius:5px;vertical-align:middle}
.imgw{position:relative;line-height:0}
.imgw img{width:100%;display:block}
.bx{position:absolute;border:1.5px solid transparent;border-radius:2px;cursor:pointer}
.bx:hover{border-color:var(--blue)}
.bx.sel{border-color:#e3a11a;background:#ffd97e44}
.bx.let{border-color:var(--let)}.bx.dia{border-color:var(--dia)}.bx.ext{border-color:var(--ext)}
.insp{padding:12px;border-top:1px solid var(--line);background:var(--soft)}
.crop{width:100%;height:auto;max-height:240px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:8px;display:block}
.reading{display:flex;align-items:center;gap:12px;margin:10px 0;flex-wrap:wrap}
.reading .big{font-family:'Amiri','Scheherazade New','Noto Naskh Arabic','Geeza Pro',serif;font-size:40px;line-height:1.4}
.reading .meta{font-size:11.5px;color:var(--mute);direction:ltr}
.marks{display:flex;gap:6px;flex-wrap:wrap;direction:ltr}
.marks button{font:inherit;font-size:12.5px;padding:7px 11px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer}
.marks button kbd{font-size:10.5px;color:var(--mute);margin-right:4px}
.marks button.on[data-k=ok]{background:var(--okbg);border-color:var(--ok)}
.marks button.on[data-k=let]{background:var(--letbg);border-color:var(--let)}
.marks button.on[data-k=dia]{background:var(--diabg);border-color:var(--dia)}
.marks button.on[data-k=ext]{background:var(--extbg);border-color:var(--ext)}
.marks button.on[data-k=miss]{background:var(--extbg);border-color:var(--ext)}
.row{display:flex;gap:8px;align-items:center;margin-top:9px;flex-wrap:wrap}
.row label{font-size:12px;color:var(--mute);direction:ltr;min-width:118px}
.row input{flex:1;min-width:160px;font:inherit;font-family:'Amiri','Geeza Pro',serif;font-size:22px;padding:4px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
.help{font-size:11.5px;color:var(--mute);direction:ltr;margin-top:10px}
.note{font-size:12px;color:var(--dia);direction:ltr;padding:8px 12px;border-bottom:1px solid var(--line);background:var(--diabg)}
</style></head><body><div class=wrap>
<header><div><h1>__BOOK__</h1><div class=sub id=sub></div></div>
<div class=ltr style="display:flex;gap:6px;flex-wrap:wrap">
<button class=btn id=imp>Import…</button><input type=file id=impf accept=".json,application/json" hidden>
<button class="btn pri" id=exp>Export marks (JSON)</button></div></header>
<div class=stats id=stats></div>
<div class=pgs id=pgs></div>
<div class=main>
 <div class=card><div class=phd><span id=ptitle></span><span style="display:flex;gap:6px">
  <button class=btn id=allok>Rest of page correct → done</button><button class=btn id=undone>Reopen page</button></span></div>
  <div id=pnote></div>
  <div class=txt id=txt></div>
  <div class=insp id=insp></div></div>
 <div class=card><div class=imgw id=imgw><img id=img alt=""><div id=boxes></div></div></div>
</div>
<div class=help>Keys: <b>Enter</b> or <b>1</b> correct + next · <b>2</b> wrong letters · <b>3</b> wrong diacritics · <b>4</b> both · <b>5</b> not on page · <b>6</b> word missing after this · <b>←/→</b> next/previous word (reading order) · <b>Esc</b> clear the mark. Type the right spelling in the box when a word is wrong. Marks save in this browser automatically; Export keeps them safe and lets Claude score them.</div>
</div>
<script>
const D=__DATA__;
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");
let S={marks:{},done:{}};
try{const v=localStorage.getItem(D.key); if(v) S=Object.assign(S,JSON.parse(v));}catch(e){}
const save=()=>{try{localStorage.setItem(D.key,JSON.stringify(S));}catch(e){} };
let cur=0, sel=0;
const imgCache={};

function loadImg(src){ if(!imgCache[src]){const im=new Image(); im.src=src; imgCache[src]=im;} return imgCache[src]; }

// words split into visual lines by box position, so the text reads like the page
function lines(p){
  const out=[]; let line=[], lastY=null, lastH=null;
  p.tokens.forEach((t,k)=>{
    const y=t.b?t.b[1]:null, h=t.b?t.b[3]:null;
    if(line.length && y!=null && lastY!=null && Math.abs(y-lastY)>Math.max(h,lastH)*0.6){out.push(line); line=[];}
    line.push(k); if(y!=null){lastY=y; lastH=h;}
  });
  if(line.length) out.push(line);
  return out;
}
function cls(m){ if(!m) return ""; if(m.k==="ok") return "ok"; if(m.k==="ext") return "ext"; if(m.let) return "let"; if(m.dia) return "dia"; if(m.k==="miss") return "ok"; return ""; }

function renderPages(){
  $("#pgs").innerHTML=D.pages.map((p,i)=>{
    const errs=p.tokens.some(t=>{const m=S.marks[t.i]; return m&&(m.let||m.dia||m.k==="ext"||m.miss);});
    return `<button data-i=${i} class="${i===cur?"on":""} ${S.done[p.page]?"done":""} ${errs?"err":""}" title="page ${p.page} (${p.path})">${p.page}</button>`;}).join("");
}
function renderPage(){
  const p=D.pages[cur];
  $("#ptitle").innerHTML=`page <b>${p.page}</b> · ${p.tokens.length} words · <span class="tag ${p.path==="keep"?"keep":""}">${p.path==="keep"?"keep: OCR diacritics are spoken":"diacritise: diacritics come from the diacritiser"}</span>${S.done[p.page]?" · <b style='color:var(--ok)'>done</b>":""}`;
  $("#pnote").innerHTML=p.path==="keep"?"":`<div class=note>This page goes to the diacritiser, which replaces the OCR's diacritics. Check the letters; diacritic marks here are noted but scored separately.</div>`;
  $("#txt").innerHTML=lines(p).map(L=>"<div>"+L.map(k=>{const t=p.tokens[k], m=S.marks[t.i];
    return `<span class="w ${cls(m)} ${k===sel?"sel":""}" data-k=${k}>${esc(m&&m.fix&&m.k!=="ok"?m.fix:t.t)}</span>`+(m&&m.miss?` <span class=miss title="missing word">+ ${esc(m.miss)}</span>`:"");
  }).join(" ")+"</div>").join("");
  const img=$("#img"); if(img.getAttribute("src")!==p.img) img.src=p.img;
  $("#boxes").innerHTML=p.tokens.map((t,k)=>t.b?`<div class="bx ${cls(S.marks[t.i])} ${k===sel?"sel":""}" data-k=${k} style="left:${t.b[0]}%;top:${t.b[1]}%;width:${t.b[2]}%;height:${t.b[3]}%"></div>`:"").join("");
  renderInsp(); renderPages(); renderStats();
}
function renderInsp(){
  const p=D.pages[cur], t=p.tokens[sel]; if(!t){$("#insp").innerHTML="";return;}
  const m=S.marks[t.i]||{};
  const on=k=>{ if(k==="ok") return m.k==="ok"; if(k==="let") return !!m.let; if(k==="dia") return !!m.dia; if(k==="ext") return m.k==="ext"; if(k==="miss") return !!m.miss; };
  $("#insp").innerHTML=`<canvas class=crop id=crop width=900 height=240></canvas>
   <div class=reading><span class=big>${esc(t.t)}</span><span class=meta>${t.i}${t.r!==t.t?` · raw OCR: <span style="font-size:15px">${esc(t.r)}</span>`:""} · word ${sel+1}/${p.tokens.length}</span></div>
   <div class=marks>
    <button data-k=ok class="${on("ok")?"on":""}"><kbd>1</kbd>correct</button>
    <button data-k=let class="${on("let")?"on":""}"><kbd>2</kbd>wrong letters</button>
    <button data-k=dia class="${on("dia")?"on":""}"><kbd>3</kbd>wrong diacritics</button>
    <button data-k=both><kbd>4</kbd>both</button>
    <button data-k=ext class="${on("ext")?"on":""}"><kbd>5</kbd>not on the page</button>
    <button data-k=miss class="${on("miss")?"on":""}"><kbd>6</kbd>word missing after this</button>
    <button data-k=clear><kbd>Esc</kbd>clear</button></div>
   <div class=row><label>right spelling</label><input id=fix dir=rtl placeholder="type the word as printed" value="${esc(m.fix||"")}"></div>
   <div class=row id=missrow style="display:${m.miss!=null?"flex":"none"}"><label>missing word(s)</label><input id=miss dir=rtl placeholder="the word the OCR skipped" value="${esc(m.miss||"")}"></div>`;
  drawCrop(p,t);
  $("#fix").oninput=e=>{const mm=S.marks[t.i]||(S.marks[t.i]={}); mm.fix=e.target.value; if(!mm.k) mm.k="err"; save(); renderStats();};
  $("#fix").onchange=()=>renderPage();
  $("#miss").oninput=e=>{const mm=S.marks[t.i]||(S.marks[t.i]={}); mm.miss=e.target.value; if(!mm.k) mm.k="ok"; save(); renderStats();};
  $("#miss").onchange=()=>renderPage();
}
function drawCrop(p,t){
  const c=$("#crop"); if(!c) return; const g=c.getContext("2d");
  const im=loadImg(p.img);
  const draw=()=>{
    g.fillStyle="#fff"; g.fillRect(0,0,c.width,c.height);
    if(!t.b){g.fillStyle="#888"; g.font="16px sans-serif"; g.fillText("no box for this word",20,40); return;}
    const W=im.naturalWidth,H=im.naturalHeight;
    let [x,y,w,h]=t.b.map((v,i)=>v/100*(i%2?H:W));
    const padY=h*0.9, padX=Math.max(h*2.2,w*0.6);     // neighbours on both sides for context
    let sx=x-padX, sy=y-padY, sw=w+2*padX, sh=h+2*padY;
    sx=Math.max(0,sx); sy=Math.max(0,sy); sw=Math.min(W-sx,sw); sh=Math.min(H-sy,sh);
    const sc=Math.min(c.width/sw,c.height/sh), dw=sw*sc, dh=sh*sc, dx=(c.width-dw)/2, dy=(c.height-dh)/2;
    g.drawImage(im,sx,sy,sw,sh,dx,dy,dw,dh);
    g.strokeStyle="#e3a11a"; g.lineWidth=3; g.strokeRect(dx+(x-sx)*sc-3,dy+(y-sy)*sc-3,w*sc+6,h*sc+6);
  };
  if(im.complete&&im.naturalWidth) draw(); else im.onload=draw;
}
function mark(k){
  const p=D.pages[cur], t=p.tokens[sel]; if(!t) return;
  const old=S.marks[t.i]||{};
  if(k==="clear"){ delete S.marks[t.i]; save(); renderPage(); return; }
  let m={fix:old.fix, miss:old.miss};
  if(k==="ok"){ m={k:"ok", miss:old.miss}; }
  else if(k==="let"){ m.k="err"; m.let=!old.let; m.dia=old.dia; }
  else if(k==="dia"){ m.k="err"; m.dia=!old.dia; m.let=old.let; }
  else if(k==="both"){ m.k="err"; m.let=true; m.dia=true; }
  else if(k==="ext"){ m={k:old.k==="ext"?undefined:"ext", miss:old.miss}; }
  else if(k==="miss"){ m=Object.assign({},old); if(m.miss==null){m.miss=""; if(!m.k) m.k="ok";} else delete m.miss; }
  if(m.k==="err"&&!m.let&&!m.dia) m.k=undefined;
  Object.keys(m).forEach(x=>m[x]===undefined&&delete m[x]);
  if(Object.keys(m).length) S.marks[t.i]=m; else delete S.marks[t.i];
  save(); renderPage();
  const focus = (m.let||m.dia) ? "#fix" : (k==="miss"&&m.miss!=null ? "#miss" : null);
  if(focus){ const el=$(focus); if(el){ el.focus(); if(!el.value&&focus==="#fix") {el.value=t.t; el.select();} } }
  else if(k==="ok") step(1);
}
function step(d){
  const p=D.pages[cur]; const n=sel+d;
  if(n>=0&&n<p.tokens.length){ sel=n; renderPage(); return; }
  if(d>0&&cur<D.pages.length-1){ cur++; sel=0; renderPage(); }
  else if(d<0&&cur>0){ cur--; sel=D.pages[cur].tokens.length-1; renderPage(); }
}

function stats(){
  // a word counts as checked if marked, or if its page is done (unmarked = correct)
  const r={words:0,checked:0,ok:0,let:0,dia:0,both:0,ext:0,miss:0,keepW:0,keepDia:0,keepLet:0,dW:0,dLet:0};
  D.pages.forEach(p=>{
    p.tokens.forEach(t=>{
      r.words++; const m=S.marks[t.i]; const counted = !!m || !!S.done[p.page];
      if(!counted) return; r.checked++;
      const L=!!(m&&m.let), Di=!!(m&&m.dia), E=!!(m&&m.k==="ext");
      if(m&&m.miss) r.miss++;
      if(E){r.ext++; return;}
      if(L) r.let++; if(Di) r.dia++; if(L&&Di) r.both++; if(!L&&!Di) r.ok++;
      if(p.path==="keep"){ r.keepW++; if(Di) r.keepDia++; if(L) r.keepLet++; }
      else { r.dW++; if(L) r.dLet++; }
    });
  });
  return r;
}
const pct=(a,b)=>b?(100*a/b).toFixed(1)+"%":"–";
function renderStats(){
  const r=stats(), done=D.pages.filter(p=>S.done[p.page]).length;
  $("#sub").textContent=`${D.engine} · ${D.pages.length} pages · ${r.words} words · ${done}/${D.pages.length} pages done · ${r.checked} words checked`;
  const errW=r.let+r.dia-r.both+r.ext+r.miss;
  $("#stats").innerHTML=`
   <div><b>${pct(errW,r.checked)}</b><span>words with any error (${errW}/${r.checked})</span></div>
   <div><b>${pct(r.let,r.checked)}</b><span>wrong letters (${r.let})</span></div>
   <div><b>${pct(r.keepDia,r.keepW)}</b><span>wrong diacritics on <i>keep</i> pages — spoken as-is (${r.keepDia}/${r.keepW})</span></div>
   <div><b>${r.ext} / ${r.miss}</b><span>extra words / missing words</span></div>
   <div><b>${pct(r.checked,r.words)}</b><span>of the book checked</span></div>`;
}

$("#pgs").onclick=e=>{const b=e.target.closest("button"); if(b){cur=+b.dataset.i; sel=0; renderPage();}};
$("#txt").onclick=e=>{const w=e.target.closest(".w"); if(w){sel=+w.dataset.k; renderPage();}};
$("#boxes").onclick=e=>{const w=e.target.closest(".bx"); if(w){sel=+w.dataset.k; renderPage();}};
$("#insp").onclick=e=>{const b=e.target.closest(".marks button"); if(b) mark(b.dataset.k);};
$("#allok").onclick=()=>{const p=D.pages[cur]; S.done[p.page]=true; save();
  if(cur<D.pages.length-1){cur++; sel=0;} renderPage();};
$("#undone").onclick=()=>{delete S.done[D.pages[cur].page]; save(); renderPage();};
document.addEventListener("keydown",e=>{
  if(e.target.tagName==="INPUT"){ if(e.key==="Enter"){e.target.blur(); renderPage(); step(1);} if(e.key==="Escape") e.target.blur(); return; }
  const map={"1":"ok","Enter":"ok","2":"let","3":"dia","4":"both","5":"ext","6":"miss","Escape":"clear"};
  if(map[e.key]){e.preventDefault(); mark(map[e.key]);}
  else if(e.key==="ArrowLeft"){e.preventDefault(); step(1);}     // RTL: left = next word
  else if(e.key==="ArrowRight"){e.preventDefault(); step(-1);}
});
$("#exp").onclick=()=>{
  const out={book:D.book, engine:D.engine, exported:new Date().toISOString(), summary:stats(), done:S.done,
    pages:D.pages.map(p=>({page:p.page,path:p.path,done:!!S.done[p.page],
      words:p.tokens.map(t=>Object.assign({id:t.i,ocr:t.t},S.marks[t.i]||{}))}))};
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:"application/json"}));
  a.download=`ocr_check_${D.book}.json`; a.click();
};
$("#imp").onclick=()=>$("#impf").click();
$("#impf").onchange=async e=>{
  const f=e.target.files[0]; if(!f) return;
  try{const o=JSON.parse(await f.text()); S={marks:{},done:o.done||{}};
    o.pages.forEach(p=>p.words.forEach(w=>{const m=Object.assign({},w); delete m.id; delete m.ocr; if(Object.keys(m).length) S.marks[w.id]=m;}));
    save(); renderPage();}catch(err){alert("Could not read that file: "+err.message);}
};
renderPage();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Build a hand-checking page for one book's OCR.")
    ap.add_argument("work_dir", help="work/<book> with OCR page records")
    ap.add_argument("--pdf", default=None, help="source PDF (default: from the records' source_pdf)")
    a = ap.parse_args()
    build(Path(a.work_dir), Path(a.pdf) if a.pdf else None)


if __name__ == "__main__":
    main()
