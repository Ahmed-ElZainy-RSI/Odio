"""Fill untimed tokens on pages synthesised before raw timestamps were persisted.

Pages 12-16 were spoken by an earlier run that didn't save Hume's word list, so
they can't be re-bound from source. Their surviving timings are still valid
anchors, though, so the gaps are interpolated the same way as everywhere else
and marked `interpolated` — no re-synthesis, no credit spend.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.speak import _interpolate

WORK = Path("work/النجوم")

for f in sorted(WORK.glob("p*.json")):
    rec = json.loads(f.read_text(encoding="utf-8"))
    if rec["status"] != "spoken" or "hume_words" in rec:
        continue
    toks = rec["tokens"]
    anchors = {i: t["t"] for i, t in enumerate(toks) if "t" in t}
    for i in anchors:
        toks[i]["t_src"] = "anchor"
    filled = _interpolate(toks, anchors, rec["duration_ms"])
    b = [t["t"][0] for t in toks]
    mono = all(b[i] <= b[i + 1] for i in range(len(b) - 1))
    rec["bind_report"] = {"hume_timestamps": None, "hume_dropped_invalid": None,
                          "tokens": len(toks), "anchored": len(anchors),
                          "interpolated": filled,
                          "untimed": sum(1 for t in toks if "t" not in t),
                          "monotonic": mono,
                          "note": "repaired without raw timestamps"}
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"p{rec['page']:03d}  {len(anchors)} anchors + {filled} interpolated "
          f"= {len(toks)}  mono={mono}")
