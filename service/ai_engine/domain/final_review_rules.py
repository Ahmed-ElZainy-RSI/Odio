"""The final context review's safety rules, as pure functions (spec 024).

A text-only reviewer reads the whole verified page and proposes residual OCR fixes. It
has no image, so it may *suggest* but never decide (constitution III): every proposal
becomes a REVIEW verdict for a person. Before any proposal is even considered, the reply
must pass the walidapro prototype's five checks -- a reply that fails one is UNSAFE, is
not retried (another semantic guess costs money and can only differ, not improve), and
yields no suggestions at all:

  1. the reply has Arabic text (and so does the input);
  2. speaker labels (``سارة:``) are untouched -- a text-only reviewer must not rename
     who is speaking;
  3. the Arabic letter count stays within 0.92..1.08 of the input (no silent deletion
     or insertion of a paragraph);
  4. the word count changes by at most 8% (small split/merge differences only);
  5. at most 18% of words change -- more means the page needs visual review, not a
     text-only rewrite.

Stdlib only, like the rest of ``domain/``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from ai_engine.domain.verify_rules import _ARABIC_LETTERS, _MARKS

MAX_CHANGED_WORD_RATIO = 0.18
MIN_ARABIC_CHAR_RATIO = 0.92
MAX_ARABIC_CHAR_RATIO = 1.08
MAX_WORD_COUNT_DELTA_RATIO = 0.08

_PAGE_COMMENT = re.compile(r"<!--\s*page\s+\d+\s*-->", re.IGNORECASE)
_HTML = re.compile(r"<[^>]+>", re.DOTALL)
_NOTE = re.compile(r"(?im)^\s*Note\s*:.*$")
_PAGE_NUMBER = re.compile(r"(?m)^\s*\d{1,4}\s*$")
_FENCE_OPEN = re.compile(r"^```(?:arabic|text|markdown)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```$")
_REPLY_PREFIXES = ("النص المصحح:", "النص المصحح", "التصحيح:", "التصحيح")
_SPEAKER_MARKS = (":", "：")


class UnsafeReview(ValueError):
    """The reply parsed, but is too aggressive to trust without visual evidence."""


def clean_ocr_noise(text: str) -> str:
    """Remove non-text noise only -- page comments, HTML, ``Note:`` lines, bare page
    numbers, runs of blank lines. Never corrects, rewrites or infers a word."""
    if not text:
        return ""
    for pattern in (_PAGE_COMMENT, _HTML, _NOTE, _PAGE_NUMBER):
        text = pattern.sub("", text)
    out: list[str] = []
    previous_empty = False
    for line in (line.rstrip() for line in text.splitlines()):
        empty = not line.strip()
        if empty and previous_empty:
            continue
        out.append(line)
        previous_empty = empty
    return "\n".join(out).strip()


def clean_reply(text: str) -> str:
    """The model's corrected text without code fences or a "النص المصحح:" preamble."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", text))
    text = text.strip()
    for prefix in _REPLY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip()
            break
    return clean_ocr_noise(text)


def _bare(text: str) -> str:
    return _MARKS.sub("", unicodedata.normalize("NFKC", text or ""))


def arabic_letter_count(text: str) -> int:
    return sum(len(w) for w in _ARABIC_LETTERS.findall(_bare(text)))


def arabic_words(text: str) -> list[str]:
    """Arabic words with their marks removed *first*. (The prototype matched letters on
    marked text, so every haraka split a word into fragments and skewed the ratios on any
    diacritized page.)"""
    return _ARABIC_LETTERS.findall(_bare(text))


def speaker_labels(words: Sequence[str]) -> list[str]:
    """Words that end in a colon: the speaker of a dialogue line."""
    return [_bare(w)[:-1] for w in words if w.endswith(_SPEAKER_MARKS) and len(w) > 1]


@dataclass(frozen=True, slots=True)
class ReviewStats:
    words_before: int
    words_after: int
    changed_words: int
    changed_ratio: float
    char_ratio: float


def check_reply(original: str, corrected: str) -> ReviewStats:
    """Apply the five checks; raise :class:`UnsafeReview` naming the first that fails."""
    if not corrected:
        raise UnsafeReview("the reviewer returned no text")
    before_chars, after_chars = arabic_letter_count(original), arabic_letter_count(corrected)
    if before_chars == 0:
        raise UnsafeReview("the page has no Arabic text to review")
    if after_chars == 0:
        raise UnsafeReview("the reviewer returned no Arabic text")
    labels_before = speaker_labels(original.split())
    labels_after = speaker_labels(corrected.split())
    if labels_before != labels_after:
        raise UnsafeReview(f"speaker labels changed: {labels_before} -> {labels_after}")
    char_ratio = after_chars / before_chars
    if not MIN_ARABIC_CHAR_RATIO <= char_ratio <= MAX_ARABIC_CHAR_RATIO:
        raise UnsafeReview(f"Arabic length changed too much (ratio {char_ratio:.3f})")
    before, after = arabic_words(original), arabic_words(corrected)
    delta_ratio = abs(len(after) - len(before)) / max(len(before), 1)
    if delta_ratio > MAX_WORD_COUNT_DELTA_RATIO:
        raise UnsafeReview(f"word count changed {len(before)} -> {len(after)} ({delta_ratio:.0%})")
    changed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    changed_ratio = changed / max(len(before), 1)
    if changed_ratio > MAX_CHANGED_WORD_RATIO:
        raise UnsafeReview(
            f"{changed}/{len(before)} words changed ({changed_ratio:.0%}): needs visual review"
        )
    return ReviewStats(len(before), len(after), changed, changed_ratio, char_ratio)


def _key(word: str) -> str:
    """What a word is, for alignment: its Arabic letters without marks, or the word itself
    when it has none (a number, Latin text)."""
    letters = "".join(_ARABIC_LETTERS.findall(_bare(word)))
    return letters or word


def suggestions(token_texts: Sequence[str], corrected: str) -> list[tuple[int, str]]:
    """``(token index, proposed text)`` for every one-for-one word change.

    Only a word replaced by exactly one word maps to a token id. A split, a merge, an
    insertion or a deletion has no single token to attach to, so it is dropped (and the
    caller logs how many). A change of marks or punctuation only is not a suggestion:
    this stage corrects letters, and adding diacritics is not its job.
    """
    proposed = corrected.split()
    before = [_key(t) for t in token_texts]
    after = [_key(w) for w in proposed]
    out: list[tuple[int, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:
            out.extend((i1 + k, proposed[j1 + k]) for k in range(i2 - i1))
    return out


def dropped_changes(token_texts: Sequence[str], corrected: str) -> int:
    """How many changed words :func:`suggestions` could not attach to a token."""
    before = [_key(t) for t in token_texts]
    after = [_key(w) for w in corrected.split()]
    dropped = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal" and not (tag == "replace" and i2 - i1 == j2 - j1):
            dropped += max(i2 - i1, j2 - j1)
    return dropped
