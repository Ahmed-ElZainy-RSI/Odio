"""The one place that bridges to the legacy pipeline packages.

`src/pipeline` is not an installed distribution -- it has no packaging and resolves only
via a directory on ``sys.path``. Confining that knowledge to a single module means the rest
of the service never learns about it, and the import-rule test can keep asserting that
``api/`` cannot reach it.

`LEGACY_REPO_ROOT` points at the repository root that contains `src/`.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from ai_engine.observability.logging import get_logger

log = get_logger(__name__)


class LegacyPipelineUnavailable(RuntimeError):
    """The legacy pipeline could not be imported. Surfaced as MODEL_UNAVAILABLE."""


@lru_cache(maxsize=8)
def ensure_legacy_on_path(repo_root: Path | None) -> Path:
    """Put ``<repo_root>/src`` on ``sys.path`` and return it.

    Cached, because repeatedly mutating ``sys.path`` in a long-lived worker is both
    wasteful and a good way to end up with duplicate entries.
    """
    if repo_root is None:
        raise LegacyPipelineUnavailable(
            "LEGACY_REPO_ROOT is not configured, so the legacy AI pipeline cannot be imported"
        )
    src = Path(repo_root).expanduser().resolve() / "src"
    if not (src / "pipeline").is_dir():
        raise LegacyPipelineUnavailable(f"no pipeline package under {src}")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
        log.info("legacy.path_added", src=str(src))
    return src


def load_extract_module(repo_root: Path | None):
    """Import ``pipeline.extract``.

    That module is unusually safe to import: its only imports are ``re`` and
    ``unicodedata``. It performs no network calls, loads no model, and -- unlike 11 other
    modules in the legacy tree -- does not call ``load_dotenv()`` at import time.
    """
    ensure_legacy_on_path(repo_root)
    try:
        from pipeline import extract  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - configuration failure
        raise LegacyPipelineUnavailable(f"could not import pipeline.extract: {exc}") from exc
    return extract
