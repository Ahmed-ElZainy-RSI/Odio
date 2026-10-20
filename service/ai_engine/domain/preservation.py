"""Letter-preservation guard for diacritization.

A diacritizer's mandate is marks only. If it changes a letter -- الفصل ("the classroom")
into الْفَضْلِ ("the merit") -- the result is a valid word, diacritized beautifully and
spoken with confidence, and nothing downstream can tell. So every diacritizer output is
compared with its input, token by token, with the marks stripped from both. Any
difference in letters, punctuation or word count rejects the whole page.

Ported from walidapro's `_check_preservation` (ocr_verifier/diacritization/rebuild_text.py),
with one deliberate difference: combining madda / hamza above / hamza below (U+0653-U+0655)
are treated as letters, not marks. Under NFC they compose with the letter before them
(ي + ◌ٔ -> ئ, ا + ◌ٔ -> أ), so a diacritizer that "adds a mark" there has changed مريي into
مرئي -- the lost-hamza error class, which is a letter error.

Stdlib only. A verbatim copy lives at src/pipeline/preservation.py; a test keeps the two
identical.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

#: What a diacritizer may add or remove: Quranic annotation signs (U+0610-U+061A),
#: tanween / harakat / shadda / sukun (U+064B-U+0652), the remaining combining marks
#: except madda and hamza (U+0656-U+065F), dagger alef (U+0670), Quranic small marks
#: (U+06D6-U+06ED) and tatweel (U+0640), which carries no letter.
_MARKS_RE = re.compile("[ؐ-ًؚ-ْٖ-ٰٟۖ-ۭـ]")


def strip_marks(text: str) -> str:
    """NFC-normalize, then remove every mark a diacritizer is allowed to touch."""
    return _MARKS_RE.sub("", unicodedata.normalize("NFC", text))


@dataclass(frozen=True, slots=True)
class FirstDifference:
    """Where a diacritizer first exceeded its mandate.

    ``index`` is the token position (the caller maps it to a token id). For a word-count
    change it is the first position past the shorter list, and the missing side is "".
    """

    index: int
    before: str
    after: str
    reason: str  # "letters_changed" | "word_count"


@dataclass(frozen=True, slots=True)
class PreservationResult:
    preserved: bool
    first_difference: FirstDifference | None = None

    def to_dict(self) -> dict[str, object]:
        diff = self.first_difference
        return {
            "preserved": self.preserved,
            "first_difference": None
            if diff is None
            else {
                "index": diff.index,
                "before": diff.before,
                "after": diff.after,
                "reason": diff.reason,
            },
        }


def check_letters_preserved(before: Sequence[str], after: Sequence[str]) -> PreservationResult:
    """True only if ``after`` equals ``before`` token by token, ignoring marks."""
    for index, (old, new) in enumerate(zip(before, after, strict=False)):
        if strip_marks(old) != strip_marks(new):
            return PreservationResult(
                preserved=False,
                first_difference=FirstDifference(index, old, new, "letters_changed"),
            )
    if len(before) != len(after):
        index = min(len(before), len(after))
        return PreservationResult(
            preserved=False,
            first_difference=FirstDifference(
                index,
                before[index] if index < len(before) else "",
                after[index] if index < len(after) else "",
                "word_count",
            ),
        )
    return PreservationResult(preserved=True)
