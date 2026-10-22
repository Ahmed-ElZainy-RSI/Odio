"""Spec 033: one codebase. The walidapro prototype was retired after its good parts were
rewritten natively (specs 006, 007, 018, 022, 030); its last state is the local tag
``walidapro-final``. Nothing may depend on it again, and it must not come back as a second
implementation of the same contract."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CODE = [REPO / "service" / "ai_engine", REPO / "src", REPO / "tools"]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_code_imports_walidapro() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for root in CODE
        if root.is_dir()
        for path in root.rglob("*.py")
        if any(n.split(".")[0] in {"walidapro", "ocr_verifier"} for n in _imports(path))
    ]
    assert offenders == []


def test_the_prototype_folder_stays_retired() -> None:
    assert not (REPO / "walidapro").exists(), (
        "walidapro/ was retired (spec 033); port what you need natively, from tag walidapro-final"
    )
