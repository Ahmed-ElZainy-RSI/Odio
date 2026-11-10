"""Fit every word box to the word's actual ink on the page image. Free, no API calls.

OCR gives one box per line, and lines_to_tokens slices it by letter count. The page image
says where the words really are: inside a line, columns with no ink are gaps. Some are
spaces between words and some sit inside a word, after a letter that doesn't connect, so
neither signal is enough alone. Gap width misreads tight spacing; letter count misreads
wide letters. The split takes the N-1 gaps that best satisfy both (_choose_gaps).

Works from pixels alone, so a book with no text layer gets the same boxes. (A PDF's own
word and letter boxes were tried first: Arabic shaping makes them unreliable, one letter's
box can span a word and a zero-width mark can sit at the far end of the line.)

A line over a busy illustration, or with too few gaps, keeps its boxes: an honest
approximation beats a confident wrong box. Only `box` changes; ids, text and timings are
never touched.

Usage:
    python src/pipeline/ink_boxes.py "work/<book>"
"""
import sys
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.records import read_record, write_record

INK_DIFF = 60            # grey levels from the line's background
MAX_INK_FRACTION = 0.45  # more than this isn't text on a plain background
MARK_GAP_PX = 4          # a harakah floats this far off its letter and still belongs to it
LINE_MARGIN_PX = 6       # room for ink just outside a line's outermost boxes
WIDTH_PENALTY = 0.15     # px of gap width a word gives up per px away from its letter share
# how far a punctuation mark can reach in from a word's edge, as a share of the line's height
WIDE_MARKS, WIDE_MARK_ROOM, NARROW_MARK_ROOM = "«»()[]", 0.55, 0.35
PAD_PX = 2               # the reader draws a 2px border inside the box; keep it off the letters


def _letters(token: dict) -> int:
    """How much room a word needs: its letters, without marks or punctuation."""
    return max(1, sum(unicodedata.category(c)[0] in "LN" for c in token["txt"]))


def _px(box: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return x * width / 100, y * height / 100, (x + w) * width / 100, (y + h) * height / 100


def _lines(tokens: list[dict]) -> list[list[dict]]:
    """Split reading-order tokens into lines: a token whose middle leaves the band starts one."""
    lines: list[list[dict]] = []
    top = bottom = 0.0
    for t in tokens:
        _, y, _, h = t["box"]
        middle = y + h / 2
        if lines and top <= middle <= bottom:
            lines[-1].append(t)
            top, bottom = min(top, y), max(bottom, y + h)
        else:
            lines.append([t])
            top, bottom = y, y + h
    return lines


def _gaps(columns: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each inkless run between ink, in column order."""
    ink = np.flatnonzero(columns)
    return [(int(a) + 1, int(b)) for a, b in zip(ink[:-1], ink[1:]) if b - a > 1]


def _choose_gaps(gaps: list[tuple[int, int]], weights: list[int], left: int, right: int
                 ) -> list[tuple[int, int]] | None:
    """The len(weights)-1 gaps, right to left, best splitting [left, right) into the words.

    weights run right to left. Each chosen gap earns its width and each word pays
    WIDTH_PENALTY per pixel it differs from its share of the line, by dynamic programming
    over the gaps taken in order.
    """
    words = len(weights)
    if len(gaps) < words - 1:
        return None
    ordered = sorted(gaps, key=lambda g: -g[0])
    total = sum(weights)
    expected = [(right - left) * w / total for w in weights]
    # best[w][g]: best score with word w ending (on its left) at gap g
    best: list[dict[int, float]] = [{} for _ in range(words)]
    back: list[dict[int, int]] = [{} for _ in range(words)]
    for g, gap in enumerate(ordered):
        best[0][g] = gap[1] - gap[0] - WIDTH_PENALTY * abs(right - gap[1] - expected[0])
    for w in range(1, words - 1):
        for g, gap in enumerate(ordered):
            candidates = [(score + gap[1] - gap[0] - WIDTH_PENALTY * abs(ordered[p][0] - gap[1] - expected[w]), p)
                          for p, score in best[w - 1].items() if p < g]
            if candidates:
                best[w][g], back[w][g] = max(candidates)
    if words == 1:
        return []
    finals = [(score - WIDTH_PENALTY * abs(ordered[g][0] - left - expected[-1]), g)
              for g, score in best[words - 2].items()]
    if not finals:
        return None
    _, g = max(finals)
    chosen = [g]
    for w in range(words - 2, 0, -1):
        g = back[w][g]
        chosen.append(g)
    return [ordered[g] for g in reversed(chosen)]


def _punctuation(token: dict) -> tuple[str, str]:
    """The punctuation before and after a word, from what OCR actually read."""
    text = token.get("raw", token["txt"])
    letters = [i for i, c in enumerate(text) if unicodedata.category(c)[0] in "LNM"]
    if not letters:
        return "", ""
    return text[:letters[0]], text[letters[-1] + 1:]


def _without_punctuation(columns: np.ndarray, first: int, last: int, token: dict, line_height: int
                         ) -> tuple[int, int]:
    """Narrow [first, last) to the word: punctuation isn't part of what gets highlighted.

    For each punctuation mark, working inwards, cut at the widest gap within the room that
    mark can take. Text runs right to left, so what comes before a word sits on its right.
    """
    before, after = _punctuation(token)

    def gaps_between(a: int, b: int) -> list[tuple[int, int]]:
        return [(g[0] + a, g[1] + a) for g in _gaps(columns[a:b])]

    for mark in before:
        room = int(line_height * (WIDE_MARK_ROOM if mark in WIDE_MARKS else NARROW_MARK_ROOM))
        near = [g for g in gaps_between(first, last) if g[1] >= last - room]
        if near:
            last = max(near, key=lambda g: g[1] - g[0])[0]
    for mark in reversed(after):
        room = int(line_height * (WIDE_MARK_ROOM if mark in WIDE_MARKS else NARROW_MARK_ROOM))
        near = [g for g in gaps_between(first, last) if g[0] <= first + room]
        if near:
            first = max(near, key=lambda g: g[1] - g[0])[1]
    return first, last


def _vertical_extent(mask: np.ndarray, core_top: int, core_bottom: int) -> tuple[int, int] | None:
    """Ink rows of a word: anything in the core band, plus marks just outside it."""
    rows = mask.any(axis=1)
    ink = np.flatnonzero(rows[core_top:core_bottom])
    if ink.size == 0:
        return None
    top, bottom = core_top + int(ink[0]), core_top + int(ink[-1]) + 1
    while rows[max(0, top - MARK_GAP_PX):top].any():
        start = max(0, top - MARK_GAP_PX)
        top = start + int(np.argmax(rows[start:top]))
    while rows[bottom:bottom + MARK_GAP_PX].any():
        bottom += int(np.flatnonzero(rows[bottom:bottom + MARK_GAP_PX])[-1]) + 1
    return top, bottom


class _Line:
    """One text line's ink, in page pixels."""

    def __init__(self, tokens: list[dict], gray: np.ndarray):
        self.height, self.width = gray.shape
        spans = [_px(t["box"], self.width, self.height) for t in tokens]
        self.x0 = max(0, int(min(s[0] for s in spans)) - LINE_MARGIN_PX)
        self.x1 = min(self.width, int(max(s[2] for s in spans)) + LINE_MARGIN_PX)
        self.core_top = int(min(s[1] for s in spans))
        self.core_bottom = int(max(s[3] for s in spans)) + 1
        # room for marks above and below; _vertical_extent only takes what sits close to the band
        pad = (self.core_bottom - self.core_top) // 3
        self.y0 = max(0, self.core_top - pad)
        y1 = min(self.height, self.core_bottom + pad)
        crop = gray[self.y0:y1, self.x0:self.x1]
        # a line OCR placed off the page leaves nothing to look at, which too_busy turns away
        self.mask = (np.abs(crop.astype(int) - int(np.median(crop))) > INK_DIFF
                     if crop.size else np.zeros(crop.shape, dtype=bool))

    @property
    def line_height(self) -> int:
        return self.core_bottom - self.core_top

    @property
    def core(self) -> np.ndarray:
        return self.mask[self.core_top - self.y0:self.core_bottom - self.y0]

    @property
    def too_busy(self) -> bool:
        return self.core.size == 0 or self.core.mean() > MAX_INK_FRACTION

    def tight(self, left: int, right: int, token: dict) -> list[float] | None:
        """Percent box around one word's ink between two page columns, padded."""
        a, b = max(self.x0, left), min(self.x1, right)
        if b <= a:
            return None
        columns = self.core[:, a - self.x0:b - self.x0].any(axis=0)
        ink = np.flatnonzero(columns)
        if ink.size == 0:
            return None
        first, last = _without_punctuation(columns, int(ink[0]), int(ink[-1]) + 1, token, self.line_height)
        part = self.mask[:, a - self.x0 + first:a - self.x0 + last]
        extent = _vertical_extent(part, self.core_top - self.y0, self.core_bottom - self.y0)
        if extent is None:
            return None
        cols = np.flatnonzero(part[extent[0]:extent[1]].any(axis=0))
        x0 = max(0, a + first + int(cols[0]) - PAD_PX)
        x1 = min(self.width, a + first + int(cols[-1]) + 1 + PAD_PX)
        top = max(0, self.y0 + extent[0] - PAD_PX)
        bottom = min(self.height, self.y0 + extent[1] + PAD_PX)
        return [round(100 * x0 / self.width, 3), round(100 * top / self.height, 3),
                round(100 * (x1 - x0) / self.width, 3), round(100 * (bottom - top) / self.height, 3)]

    def split(self, tokens: list[dict]) -> dict[str, list[float]]:
        """The line's words, right to left, cut at the chosen gaps."""
        columns = self.core.any(axis=0)
        ink = np.flatnonzero(columns)
        if ink.size == 0:
            return {}
        left, right = int(ink[0]) + self.x0, int(ink[-1]) + 1 + self.x0
        gaps = [(a + self.x0, b + self.x0) for a, b in _gaps(columns)]
        chosen = _choose_gaps(gaps, [_letters(t) for t in tokens], left, right)
        if chosen is None:
            return {}
        out = {}
        edge = right
        for k, t in enumerate(tokens):
            next_edge = chosen[k][1] if k < len(chosen) else left
            box = self.tight(next_edge, edge, t)
            if box is not None:
                out[t["id"]] = box
            edge = chosen[k][0] if k < len(chosen) else left
        return out


def _fit_line(tokens: list[dict], gray: np.ndarray) -> dict[str, list[float]]:
    line = _Line(tokens, gray)
    return {} if line.too_busy else line.split(tokens)


def fit_boxes(tokens: list[dict], gray: np.ndarray) -> dict[str, list[float]]:
    """token id -> box tight on the word's ink. Tokens are in reading order, line by line."""
    boxes = {}
    for line in _lines(tokens):
        boxes.update(_fit_line(line, gray))
    return boxes


def fit_page(rec: dict, image: Path) -> dict:
    """Apply the fit to one record, in place. Returns {fitted, total}."""
    try:
        gray = np.asarray(Image.open(image).convert("L"))
    except OSError:  # an unreadable page image costs the page its tight boxes, not the book
        return {"fitted": 0, "total": len(rec["tokens"])}
    boxes = fit_boxes(rec["tokens"], gray)
    for t in rec["tokens"]:
        if t["id"] in boxes:
            t["box"] = boxes[t["id"]]
    report = {"fitted": len(boxes), "total": len(rec["tokens"])}
    rec["box_fit"] = report
    if rec["tokens"] and len(boxes) == len(rec["tokens"]):
        rec["box_level"] = "word"
    return report


def run(work_dir: Path) -> dict:
    totals = {"fitted": 0, "total": 0}
    for path in sorted(work_dir.glob("p[0-9][0-9][0-9].json")):
        rec = read_record(path)
        image = work_dir / "pages" / f"p{rec['page']:03d}.jpg"
        if "box_fit" in rec or not image.exists():
            continue
        report = fit_page(rec, image)
        write_record(path, rec)
        print(f"  p{rec['page']:03d}  {report['fitted']:>3}/{report['total']:<3} fitted")
        for key in totals:
            totals[key] += report[key]
    print(f"\n{totals['fitted']}/{totals['total']} tokens fitted to their ink")
    return totals


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run(Path(sys.argv[1]))
