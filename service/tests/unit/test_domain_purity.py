"""The domain layer must be importable with no third-party packages installed.

This is not a style rule. It is the test of whether the layering is real: if `domain/`
can only be imported alongside pydantic and SQLAlchemy, then the business rules are
entangled with infrastructure and the INVARIANT-1 predicates cannot be tested in
isolation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[2] / "ai_engine" / "domain"

STDLIB = set(sys.stdlib_module_names)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_imports_only_stdlib_and_itself() -> None:
    offenders: dict[str, set[str]] = {}
    for module in sorted(DOMAIN.glob("*.py")):
        roots = _imported_roots(module)
        foreign = {r for r in roots if r not in STDLIB and r != "ai_engine"}
        if foreign:
            offenders[module.name] = foreign
    assert not offenders, f"domain must not import third-party packages: {offenders}"


def test_api_layer_never_imports_adapters_or_legacy_pipelines() -> None:
    """The API process must not be able to reach a model implementation.

    This is the strongest possible protection for the AI stages: the attack surface does
    not exist, because the code is not importable from a request handler.
    """
    api_dir = DOMAIN.parent / "api"
    offenders: dict[str, set[str]] = {}
    for module in sorted(api_dir.rglob("*.py")):
        text = module.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(module))
        bad: set[str] = set()
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                # `api.app` legitimately wires the default in-memory store at the process
                # edge; nothing else in api/ may touch adapters or the legacy pipelines.
                if name.startswith(("pipeline", "walidapro", "tools")):
                    bad.add(name)
                if "adapters" in name and module.name != "app.py":
                    bad.add(name)
        if bad:
            offenders[str(module.relative_to(api_dir))] = bad
    assert not offenders, f"api must not import adapters or legacy pipelines: {offenders}"


def test_no_load_dotenv_anywhere_in_the_service_package() -> None:
    """`load_dotenv()` at import time is how injected secrets get silently overridden.

    The legacy modules do it in 11 places. This package must never do it.

    Checks the AST for real imports and calls, so documentation that merely *mentions*
    the anti-pattern does not trip the guard.
    """
    package = DOMAIN.parent
    offenders: dict[str, set[str]] = {}
    for module in sorted(package.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dotenv"):
                found.add(f"from {node.module} import ...")
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names if a.name.split(".")[0] == "dotenv")
            elif isinstance(node, ast.Call):
                target = node.func
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else ""
                )
                if name == "load_dotenv":
                    found.add("load_dotenv()")
        if found:
            offenders[str(module.relative_to(package))] = found
    assert not offenders, f"load_dotenv must not be used in ai_engine/: {offenders}"
