"""Fingerprints for the paid-call cache (spec 021).

A cached result may be reused only if *everything that could change it* is the same: the
provider, the model, the operation, every parameter, and the exact input bytes. The key
is a SHA-256 of a canonical JSON of all of those, so leaving one out is the only way to
serve a stale result -- which is why callers pass inputs explicitly instead of the cache
guessing. ``CACHE_FORMAT`` is part of every key: bumping it invalidates everything.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

CACHE_FORMAT = 1

Param = str | int | float | bool | None


def digest(data: bytes | str) -> str:
    """SHA-256 of an input, for use as a fingerprint component."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def fingerprint(*, provider: str, model: str, operation: str, params: Mapping[str, Param]) -> str:
    """A stable cache key. Any change to any argument changes the key."""
    if not provider or not model or not operation:
        raise ValueError("provider, model and operation are all required")
    body = {
        "format": CACHE_FORMAT,
        "provider": provider,
        "model": model,
        "operation": operation,
        "params": dict(params),
    }
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
