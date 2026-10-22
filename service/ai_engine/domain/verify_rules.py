"""The verified tier's decision rules, as pure functions (spec 022).

Rewritten from the walidapro prototype's verification engine -- its rules are the part
worth keeping, its code is not imported or depended on. What these rules guarantee:

  - **context alone never changes letters**: a high context score with weak visual
    support is capped below the accept threshold;
  - **UNCLEAR is zero evidence, not weak evidence**: a blind reading below 0.72 confidence,
    that isn't exactly one Arabic word, or that sits between original and candidate,
    contributes nothing;
  - **the reader never sees the answer**: similarity to original/candidate is computed here,
    after the blind reading comes back;
  - a suspect is resolved to exactly one stable token id, or it isn't changed at all.

Stdlib only, like the rest of ``domain/``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

#: harakat, Quranic marks, dagger alef
_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
#: one Arabic word: letters plus the marks that may sit on them
_ARABIC_WORD = re.compile(
    "[\u0621-\u063a\u0641-\u064a\u0671-\u06d3\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]+"
)
_ARABIC_LETTERS = re.compile("[\u0621-\u063a\u0641-\u064a\u0671-\u06d3]+")
_ALEF_FORMS = str.maketrans(
    {
        "\u0623": "\u0627",
        "\u0625": "\u0627",
        "\u0622": "\u0627",
        "\u0671": "\u0627",
        "\u0649": "\u064a",
    }
)
_TATWEEL = "\u0640"

ACCEPT, REVIEW, REJECT = "ACCEPT", "REVIEW", "REJECT"


def normalize(text: str) -> str:
    """Fold for comparison only: NFKC, no marks or tatweel, alef forms and alef maqsura unified."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _MARKS.sub("", text).replace(_TATWEEL, "")
    return text.translate(_ALEF_FORMS).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def same_letters(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)


def _clamp(x: float | None) -> float:
    return max(0.0, min(1.0, float(x or 0.0)))


# ── the blind reader's gates ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BlindRead:
    reading: str
    confidence: float
    supports_original: float
    supports_candidate: float
    unclear: bool


def gate_blind_read(
    reading: str,
    confidence: float,
    unclear: bool,
    *,
    original: str,
    candidate: str | None,
    min_confidence: float = 0.72,
    min_gap: float = 0.12,
    min_support: float = 0.82,
) -> BlindRead:
    """Turn a raw blind reading into evidence -- or into nothing.

    The reader saw only the crop. Its reading counts as evidence only if it is confident,
    is exactly one Arabic word, clearly favours one side, and strongly matches it. Anything
    less is UNCLEAR, and UNCLEAR supports neither side.
    """
    reading = (reading or "").strip()
    confidence = _clamp(confidence)
    if not reading:
        return BlindRead("", 0.0, 0.0, 0.0, True)
    if unclear:
        confidence = min(confidence, 0.49)
    if confidence < min_confidence:
        unclear = True
    words = _ARABIC_WORD.findall(reading)
    if len(words) != 1:
        unclear = True
    else:
        reading = words[0]
    sup_o = similarity(reading, original)
    sup_c = similarity(reading, candidate) if candidate else 0.0
    ambiguous = abs(sup_c - sup_o) < min_gap or max(sup_o, sup_c) < min_support
    if candidate and ambiguous:
        unclear = True
    if unclear:
        sup_o = sup_c = 0.0
    return BlindRead(reading, confidence, sup_o, sup_c, unclear)


# ── LightOn re-reads of the crop variants ───────────────────────────────────────

VARIANT_ORDER = ("original", "upscaled", "contrast", "sharp", "threshold")


def first_arabic_word(text: str) -> str:
    words = _ARABIC_LETTERS.findall(_MARKS.sub("", text or ""))
    return words[0] if words else ""


def enough_votes(readings_so_far: Sequence[str], *, minimum_reads: int = 2) -> bool:
    """The adaptive early stop: after at least two reads, stop once any normalised reading
    has two votes. It never looks at the candidate, so it can't be biased toward it."""
    if len(readings_so_far) < minimum_reads:
        return False
    votes: dict[str, int] = defaultdict(int)
    for r in readings_so_far:
        word = first_arabic_word(r)
        if word:
            votes[normalize(word)] += 1
    return bool(votes) and max(votes.values()) >= 2


def _closest_word(raw: str, candidate: str, original: str) -> tuple[str, float]:
    words = _ARABIC_LETTERS.findall(_MARKS.sub("", raw or ""))
    if not words:
        return raw.strip(), 0.0
    best = max(words, key=lambda w: max(similarity(w, candidate), similarity(w, original)))
    return best, similarity(candidate, best)


def visual_consensus(
    readings: Mapping[str, str], *, candidate: str, original: str
) -> tuple[str, float, list[str]]:
    """The best-agreed reading across variants: (word, candidate support, variants agreeing)."""
    cleaned = []
    for variant, raw in readings.items():
        if not raw:
            continue
        word, cand_score = _closest_word(raw, candidate, original)
        if word:
            cleaned.append((variant, word, cand_score, similarity(original, word)))
    if not cleaned:
        return "", 0.0, []
    groups: dict[str, list] = defaultdict(list)
    for item in cleaned:
        key = normalize(item[1])
        if key:
            groups[key].append(item)
    best: tuple[float, str, float, list[str]] = (-1.0, "", 0.0, [])
    for group in groups.values():
        n = len(group)
        avg_c = sum(i[2] for i in group) / n
        avg_o = sum(i[3] for i in group) / n
        score = n / len(cleaned) * 0.70 + max(avg_c, avg_o) * 0.20 + abs(avg_c - avg_o) * 0.10
        if score > best[0]:
            best = (score, group[0][1], avg_c, [i[0] for i in group])
    return best[1], _clamp(best[2]), best[3]


# ── the decision ────────────────────────────────────────────────────────────────


def decide(
    *,
    original: str,
    candidate: str,
    context_score: float,
    validation_score: float,
    lighton_reading: str,
    lighton_score: float,
    blind: BlindRead,
    accept_threshold: float = 0.88,
    review_threshold: float = 0.70,
) -> tuple[float, str]:
    """Fuse context, validation and two independent visual readings into ACCEPT/REVIEW/REJECT.

    Rule order matters and is the prototype's, unchanged: visual evidence for the original
    vetoes first; the strongest agreement accepts; an unclear blind read can never accept a
    letter change; a generic accept still needs clear visual support for the candidate.
    """
    context_score, validation_score = _clamp(context_score), _clamp(validation_score)
    lighton_score = _clamp(lighton_score)
    letters_change = not same_letters(original, candidate)

    lo_orig = similarity(original, lighton_reading)
    lo_cand = similarity(candidate, lighton_reading)
    if blind.unclear:
        g_cand = g_orig = 0.0
    else:
        read_o = similarity(original, blind.reading) if blind.reading else 0.0
        read_c = similarity(candidate, blind.reading) if blind.reading else 0.0
        weight = 0.50 + blind.confidence * 0.50
        g_cand = (blind.supports_candidate * 0.55 + read_c * 0.45) * weight
        g_orig = (blind.supports_original * 0.55 + read_o * 0.45) * weight
    cand_consensus = lo_cand * 0.50 + g_cand * 0.50
    orig_consensus = lo_orig * 0.50 + g_orig * 0.50

    def capped_review() -> tuple[float, str]:
        final = context_score * 0.25 + cand_consensus * 0.45 + validation_score * 0.30
        return min(final, review_threshold + 0.05), REVIEW

    # 1. both visual readers see the original
    if lo_orig >= 0.90 and g_orig >= 0.85 and orig_consensus > cand_consensus and letters_change:
        return capped_review()
    # 2. visual evidence clearly closer to the original
    if orig_consensus >= 0.80 and orig_consensus - cand_consensus >= 0.12:
        return capped_review()
    # 3. everything agrees on the candidate
    if lo_cand >= 0.90 and g_cand >= 0.90 and context_score >= 0.90 and validation_score >= 0.90:
        final = context_score * 0.20 + lo_cand * 0.25 + g_cand * 0.35 + validation_score * 0.20
        return min(1.0, final), ACCEPT
    # 4. a very clear blind read, strong context, LightOn not backing the original
    if (
        g_cand >= 0.92
        and blind.confidence >= 0.90
        and lo_orig < 0.90
        and context_score >= 0.92
        and validation_score >= 0.90
    ):
        final = context_score * 0.25 + lo_cand * 0.15 + g_cand * 0.40 + validation_score * 0.20
        if final >= accept_threshold:
            return min(1.0, final), ACCEPT
    # 5. hard rule: an unclear blind read cannot accept a letter change
    if blind.unclear and letters_change:
        conservative = context_score * 0.20 + lo_cand * 0.35 + validation_score * 0.20
        conservative = _clamp(min(conservative, accept_threshold - 0.01))
        if lo_cand >= 0.92 and context_score >= 0.90 and validation_score >= 0.90:
            return conservative, REVIEW
        return min(conservative, review_threshold - 0.01), REJECT

    final = _clamp(
        context_score * 0.25 + lighton_score * 0.20 + g_cand * 0.35 + validation_score * 0.20
    )
    if cand_consensus < 0.60 and letters_change:
        if final >= review_threshold:
            return min(final, accept_threshold - 0.01), REVIEW
        return final, REJECT
    # high context alone can't change letters: the candidate needs clear visual support
    weak_visual = cand_consensus < 0.82 or cand_consensus - orig_consensus < 0.12 or blind.unclear
    if final >= accept_threshold and letters_change and weak_visual:
        return min(final, accept_threshold - 0.01), REVIEW
    if final >= accept_threshold:
        return final, ACCEPT
    if final >= review_threshold:
        return final, REVIEW
    return final, REJECT


# ── suspects from the reviewer, and where they are ─────────────────────────────


@dataclass(frozen=True, slots=True)
class Suspect:
    word: str
    sentence: str
    score: float
    reason: str
    candidate: str | None


def filter_suspects(
    raw_items: Sequence[Mapping], page_text: str, *, threshold: float = 0.75
) -> list[Suspect]:
    """Keep only what the reviewer can back up: the word must appear literally in the page
    and in its sentence, and the score must clear the threshold."""
    out = []
    for raw in raw_items:
        try:
            word = str(raw.get("word", "")).strip()
            sentence = str(raw.get("sentence", "")).strip()
            score = float(raw.get("score", 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
        if not word or not sentence or word not in page_text or word not in sentence:
            continue
        if score < threshold:
            continue
        candidate = raw.get("candidate")
        candidate = str(candidate).strip() if candidate is not None else None
        if not candidate or candidate.lower() == "null":
            candidate = None
        out.append(
            Suspect(word, sentence, _clamp(score), str(raw.get("reason", "")).strip(), candidate)
        )
    return out


def resolve_token(word: str, sentence: str, token_texts: Sequence[str]) -> int | None:
    """The index of the one token this suspect refers to, or None if it isn't unambiguous.

    Replaces the prototype's character-span matching: matching is on whole tokens (so a word
    never matches inside a longer one), and a word that occurs more than once on the page is
    pinned by its sentence -- which must itself occur exactly once.
    """
    target = normalize(first_arabic_word(word) or word)
    if not target:
        return None
    norm = [normalize(first_arabic_word(t) or t) for t in token_texts]
    hits = [i for i, t in enumerate(norm) if t == target]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    sent = [normalize(first_arabic_word(w) or w) for w in sentence.split()]
    sent = [w for w in sent if w]
    if not sent:
        return None
    starts = [i for i in range(len(norm) - len(sent) + 1) if norm[i : i + len(sent)] == sent]
    if len(starts) != 1:
        return None
    inside = [starts[0] + j for j, w in enumerate(sent) if w == target]
    return inside[0] if len(inside) == 1 else None


# ── locating a word on the page image ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LocatedWord:
    text: str
    score: float
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 in page-image pixels


def best_box(word: str, located: Sequence[LocatedWord]) -> tuple[LocatedWord | None, float]:
    """The located word that best matches `word`: exact (after folding) wins outright,
    otherwise fuzzy similarity plus a small bonus for the locator's own confidence."""
    target = normalize(word)
    if not target:
        return None, 0.0
    best, best_score = None, 0.0
    for item in located:
        text = normalize(item.text)
        if not text:
            continue
        if text == target:
            return item, 1.0
        score = min(SequenceMatcher(None, target, text).ratio() + _clamp(item.score) * 0.05, 1.0)
        if score > best_score:
            best, best_score = item, score
    return best, best_score
