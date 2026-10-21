"""Job A diacritisation with the local CPU model (spec 016).

Wraps the same engine the CLI uses (``arabic-diacritizer``: BiLSTM + attention, MIT,
CPU-only, 6.6% DER on Tashkeela) with the same rules as ``src/pipeline/tashkel.py``:

  - the whole page goes in as one string, for sentence context;
  - output words are matched back to token ids **by position**, never re-tokenised;
  - a page whose word count changes, or whose letters change (spec 006), is left exactly
    as it was -- all or nothing -- and reported as needing review.

Unlike the legacy module, the model belongs to the instance, not a module global, so a
worker owns its weights explicitly and tests can inject a fake.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from ai_engine.domain.errors import PROGRAMMING_ERRORS, ModelUnavailableError
from ai_engine.domain.preservation import check_letters_preserved
from ai_engine.domain.tokens import Token
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)


def _load_pretrained() -> Any:
    try:
        from diacritize import Diacritizer  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ModelUnavailableError(
            "DIACRITIZER=local but the arabic-diacritizer package is not installed"
        ) from exc
    return Diacritizer.from_pretrained()


class LocalDiacritizer:
    """Implements :class:`~ai_engine.ports.ai.Diacritizer`."""

    name = "arabic-diacritizer"
    model_id = "arabic-diacritizer-1.0"
    provider = "local"

    def __init__(self, *, loader: Callable[[], Any] = _load_pretrained) -> None:
        self._loader = loader
        self._model: Any = None

    def load(self) -> None:
        """Load the weights now. The worker calls this at startup to fail fast."""
        if self._model is None:
            self._model = self._loader()

    def diacritize(
        self, tokens: Sequence[Token], *, page_image: bytes | None, page_number: int
    ) -> tuple[Sequence[Token], float]:
        """Returns (tokens, fraction needing review): 0.0 applied, 1.0 page left as-is."""
        words = [t.text for t in tokens]
        if not words:
            return tokens, 0.0
        self.load()
        try:
            out = self._model.diacritize(" ".join(words)).split()
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:  # the model is local: no retry, the page keeps its text
            log.warning(
                "diacritize.model_failed", page_number=page_number, error=type(exc).__name__
            )
            return tokens, 1.0

        if len(out) != len(words):
            log.warning(
                "diacritize.word_count_mismatch",
                page_number=page_number,
                expected=len(words),
                got=len(out),
            )
            return tokens, 1.0
        check = check_letters_preserved(words, out)
        if not check.preserved:
            diff = check.first_difference
            assert diff is not None
            log.warning(
                "diacritize.letters_changed",
                page_number=page_number,
                token_id=tokens[diff.index].id,
                reason=diff.reason,
            )
            return tokens, 1.0
        return [replace(t, text=w) for t, w in zip(tokens, out, strict=True)], 0.0


class SyntheticDiacritizer:
    """A free, deterministic stand-in for tests: a fatha after every Arabic letter."""

    name = "synthetic"
    model_id = "synthetic"
    provider = "local"

    def load(self) -> None:
        return None

    def diacritize(
        self, tokens: Sequence[Token], *, page_image: bytes | None, page_number: int
    ) -> tuple[Sequence[Token], float]:
        def mark(text: str) -> str:
            return "".join(c + "\u064e" if "\u0621" <= c <= "\u064a" else c for c in text)

        return [replace(t, text=mark(t.text)) for t in tokens], 0.0
