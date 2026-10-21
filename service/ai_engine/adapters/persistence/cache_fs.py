"""Filesystem implementation of the paid-call cache (spec 021).

One file per entry under ``<root>/<key[:2]>/<key>``, written to a temporary sibling and
renamed into place, so a crash mid-write leaves the previous entry or none. Correct for
one host (or a shared volume); a fleet-wide cache would live in the job store instead.

``max_age_seconds`` bounds how long an entry is trusted; ``None`` keeps entries until the
directory is cleared (results are pure functions of their key, so age only matters when a
provider silently changes a model behind the same name).
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from ai_engine.observability.logging import get_logger

log = get_logger(__name__)


class FileProviderCache:
    """Implements :class:`~ai_engine.ports.cache.ProviderCache`."""

    def __init__(self, root: Path, *, max_age_seconds: float | None = None) -> None:
        self._root = Path(root)
        self._max_age = max_age_seconds

    def _path(self, key: str) -> Path:
        if len(key) < 3 or not key.isalnum():
            raise ValueError(f"not a cache key: {key!r}")
        return self._root / key[:2] / key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            if self._max_age is not None and time.time() - path.stat().st_mtime > self._max_age:
                return None
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:  # unreadable entry: a miss, not a failed page
            log.warning("cache.read_failed", key=key[:12], error=type(exc).__name__)
            return None

    def put(self, key: str, value: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(value)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
