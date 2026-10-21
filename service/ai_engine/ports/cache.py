"""The paid-call cache port (spec 021)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProviderCache(Protocol):
    """Opaque bytes by fingerprint (``domain.cache_key.fingerprint``).

    Contract:
      - ``put`` is atomic: a crash mid-write leaves the old entry or none, never a torn one.
      - ``get`` returns None for a miss, and for an entry it cannot read -- a damaged
        entry is a miss, never an error that fails the page.
      - Callers store only *validated* results. A provider error is never cached.
    """

    def get(self, key: str) -> bytes | None: ...

    def put(self, key: str, value: bytes) -> None: ...
