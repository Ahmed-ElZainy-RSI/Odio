"""Layers 05-06 — synthesise a page and bind word timings back to token ids.

Hume emits word-level timestamps, but measured on this book it does not emit one
per word: page 14 returned 20 timestamps for 29 tokens, with the first spanning
5.2s. So binding has two jobs, kept deliberately separate and both recorded:

  ANCHOR       tokens Hume timed directly. Exact, monotonic, trustworthy.
  INTERPOLATE  tokens it skipped. Timing is shared out between the surrounding
               anchors in proportion to letter count.

Every token records which it was (`t_src`), so the reader — and anyone auditing
the index — can tell measured timings from inferred ones.
"""
import re
import unicodedata

LETTERS = re.compile(r"[^ء-ي]")


def key(s: str) -> str:
    return LETTERS.sub("", unicodedata.normalize("NFC", s))


def _sanitise(hume_words):
    """Drop timestamps the vendor got wrong.

    Measured on this book, Hume emitted 'كريم' [12920, 5240] — begin after end —
    and a later word starting before it. One bad interval poisons every
    interpolated span around it, so anything inverted or out of order against
    the running maximum is discarded rather than trusted.
    """
    out, last_begin, dropped = [], -1, []
    for hw in hume_words:
        # inverted interval, or a start that moves backwards in time
        if hw["e"] < hw["b"] or hw["b"] < last_begin:
            dropped.append(hw)
            continue
        out.append(hw)
        last_begin = hw["b"]
    return out, dropped


def _match_forward(tokens, hume_words, window=6):
    """Greedy in-order match, bounded lookahead.

    An unbounded search lets one bad match run the pointer to the end of the
    page and scatter later timings backwards — which is exactly what produced
    non-monotonic output on page 14. The window keeps a miss local.
    """
    anchors = {}
    ti = 0
    for hw in hume_words:
        hk = key(hw["text"])
        if not hk:
            continue
        for j in range(ti, min(ti + window, len(tokens))):
            if key(tokens[j]["txt"]) == hk:
                anchors[j] = [hw["b"], hw["e"]]
                ti = j + 1
                break
    return anchors


def _interpolate(tokens, anchors, duration_ms):
    """Fill untimed tokens between anchors, proportional to letter count."""
    n = len(tokens)
    idx = sorted(anchors)
    if not idx:
        return 0
    filled = 0
    prev_i, prev_t = -1, 0
    for i in idx + [n]:
        end_t = anchors[i][0] if i < n else duration_ms
        gap = [j for j in range(prev_i + 1, i)]
        if gap:
            # Neighbouring speech overlaps, so the previous anchor can end after
            # the next one begins. Starting a gap there would emit begins that
            # run past the following anchor and break playback ordering.
            start = min(prev_t, end_t)
            weights = [max(1, len(key(tokens[j]["txt"]))) for j in gap]
            total = sum(weights)
            t = start
            avail = max(0, end_t - start)
            for j, w in zip(gap, weights):
                dur = avail * w / total
                tokens[j]["t"] = [int(t), int(t + dur)]
                tokens[j]["t_src"] = "interpolated"
                t += dur
                filled += 1
        if i < n:
            tokens[i]["t"] = anchors[i]
            tokens[i]["t_src"] = "anchor"
            # adjacent speech often overlaps slightly; never let the cursor sit
            # past the next anchor or interpolated spans invert
            prev_i, prev_t = i, max(anchors[i][0], anchors[i][1])
    return filled


def bind(tokens, hume_words, duration_ms):
    """Attach t=[begin,end] and t_src to every token. Returns (tokens, report)."""
    clean_words, dropped = _sanitise(hume_words)
    anchors = _match_forward(tokens, clean_words)
    filled = _interpolate(tokens, anchors, duration_ms)

    times = [t["t"] for t in tokens if "t" in t]
    monotonic = all(times[i][0] <= times[i + 1][0] for i in range(len(times) - 1))
    return tokens, {
        "hume_timestamps": len(hume_words),
        "hume_dropped_invalid": len(dropped),
        "tokens": len(tokens),
        "anchored": len(anchors),
        "interpolated": filled,
        "untimed": sum(1 for t in tokens if "t" not in t),
        "monotonic": monotonic,
    }
