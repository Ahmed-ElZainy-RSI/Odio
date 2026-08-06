"""Re-bind timings from persisted hume_words — no API calls, no credit spend."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.speak import bind

WORK = Path("work/النجوم")
print(f"{'page':>5} {'hume':>5} {'bad':>4} {'anch':>5} {'interp':>7} {'tokens':>7}  monotonic")
print("-"*54)
for f in sorted(WORK.glob("p*.json")):
    rec = json.loads(f.read_text(encoding="utf-8"))
    if "hume_words" not in rec:
        continue
    for t in rec["tokens"]:
        t.pop("t", None); t.pop("t_src", None)
    tokens, rep = bind(rec["tokens"], rec["hume_words"], rec["duration_ms"])
    rec["tokens"], rec["bind_report"] = tokens, rep
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{rec['page']:>5} {rep['hume_timestamps']:>5} {rep['hume_dropped_invalid']:>4} "
          f"{rep['anchored']:>5} {rep['interpolated']:>7} {rep['tokens']:>7}  "
          f"{'yes' if rep['monotonic'] else 'NO'}")
