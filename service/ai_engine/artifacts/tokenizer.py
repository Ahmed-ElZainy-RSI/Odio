"""The deterministic tokenizer shared by both job types.

This is the hinge between Job A and Job B, and it works because of one property of the
Text artifact: it is built as ``" ".join(token.text for token in tokens)``. Since no token
may contain whitespace (enforced in :class:`~ai_engine.domain.tokens.Token`), the join is
losslessly invertible by ``str.split()``.

So Job B can reproduce Job A's exact token ids from the text file alone, given only the
`pageNumber` the Backend supplies -- no cross-job state, no correlation table.
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_engine.domain.tokens import Token, token_id


def tokenize(text: str, page_number: int) -> list[Token]:
    """Split page text into tokens with stable, reproducible ids.

    Pure and side-effect free. Called by Job B on the downloaded Text artifact, and by the
    tests that prove Job A -> Job B id stability.
    """
    words = text.split()
    return [Token(id=token_id(page_number, index), text=word) for index, word in enumerate(words)]


def detokenize(tokens: Sequence[Token]) -> str:
    """Join tokens back into page text. Inverse of :func:`tokenize`."""
    return " ".join(token.text for token in tokens)
