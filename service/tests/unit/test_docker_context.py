"""The image's build context admits everything the Dockerfile copies.

The root ``.dockerignore`` denies everything and re-admits a few directories. A ``COPY``
of anything else fails only at ``docker build`` time -- which is how spec 025's
``COPY config`` shipped broken. This catches it in the ordinary test run.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _copied_sources() -> set[str]:
    sources: set[str] = set()
    for line in (REPO / "service" / "Dockerfile").read_text().splitlines():
        parts = line.split()
        if not parts or parts[0] != "COPY" or any(p.startswith("--from") for p in parts):
            continue
        sources.update(parts[1:-1])
    return sources


def _admitted_roots() -> set[str]:
    rules = (REPO / ".dockerignore").read_text().splitlines()
    return {m.group(1) for r in rules if (m := re.fullmatch(r"!([^/*]+)/?", r.strip()))}


def test_the_dockerfile_copies_something_from_the_context() -> None:
    assert "src" in _copied_sources()


def test_every_copied_path_is_admitted_by_dockerignore() -> None:
    admitted = _admitted_roots()
    missing = sorted(s for s in _copied_sources() if s.split("/")[0] not in admitted)
    assert missing == [], f"excluded by .dockerignore: {missing}"


def test_every_copied_path_exists() -> None:
    assert sorted(s for s in _copied_sources() if not (REPO / s).exists()) == []
