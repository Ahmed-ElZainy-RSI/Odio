"""Shared --pages spec parsing, used by every driver so 'first N' and 'a
range' behave identically everywhere instead of each driver reinventing it.

Accepted forms (1-indexed, inclusive, matching the p001.json page numbering
used throughout work/<book>/):
    "0" or "" or None  -> all pages
    "5"                -> pages 1-5
    "13:20"            -> pages 13-20
    "13:"              -> page 13 to the end
    ":20"               -> pages 1-20 (same as "20")
"""


def parse_pages(spec) -> tuple[int, int]:
    """Returns (start, end), both 1-indexed inclusive. end=0 means no upper bound."""
    if spec is None:
        return 1, 0
    spec = str(spec).strip()
    if not spec or spec == "0":
        return 1, 0
    if ":" in spec:
        a, b = spec.split(":", 1)
        start = int(a) if a.strip() else 1
        end = int(b) if b.strip() else 0
        return start, end
    return 1, int(spec)


def in_range(page: int, start: int, end: int) -> bool:
    return page >= start and (end == 0 or page <= end)
