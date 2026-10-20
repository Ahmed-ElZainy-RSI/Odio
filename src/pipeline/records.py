"""Read, write and validate page records -- work/<book>/pNNN.json (spec 009).

The page record is the single source of truth for a page, and nothing used to check
its shape. A typo in a status breaks resume without an error. Several past bugs were
resume checks that didn't recognise a status (see run_tashkel.py, run_ocr_gemini.py). So
every writer in src/pipeline and tools/ goes through write_record(), which validates
against page-record.schema.json first. A test enforces that no other code writes a page
record directly.

PAGE_RECORD_VALIDATION controls what an invalid record does:
  strict -- raise RecordValidationError and leave the file on disk untouched (tests)
  warn   -- print the problems and write anyway (default: a run is never lost to this)
  off    -- no validation

The full JSON Schema check needs `jsonschema`, which the vx2lm env has. Without it,
a smaller check driven by the same schema file still catches what breaks resume:
required fields, the status/path/source enums, token ids and whitespace in txt.

Usage (audit existing books, read-only):
    python3 src/pipeline/records.py work/
"""
import json
import os
import re
import sys
from functools import cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("page-record.schema.json")
PAGE_FILE = re.compile(r"^p\d{3,}\.json$")


class RecordValidationError(ValueError):
    pass


@cache
def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def _jsonschema_validator():
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return None
    return Draft202012Validator(_schema())


def _fallback_errors(rec: dict) -> list[str]:
    schema = _schema()
    props, token = schema["properties"], schema["$defs"]["token"]
    id_re = re.compile(schema["$defs"]["tokenId"]["pattern"])
    txt_re = re.compile(token["properties"]["txt"]["pattern"])
    errors = [f": '{k}' is a required property" for k in schema["required"] if k not in rec]
    if "page" in rec and (not isinstance(rec["page"], int) or rec["page"] < 1):
        errors.append(f"page: {rec['page']!r} is not a page number")
    if "diac_ratio" in rec and (isinstance(rec["diac_ratio"], bool)
                                or not isinstance(rec["diac_ratio"], (int, float))):
        errors.append(f"diac_ratio: {rec['diac_ratio']!r} is not a number")
    for key in ("status", "path", "source", "box_level"):
        if key in rec and rec[key] not in props[key]["enum"]:
            errors.append(f"{key}: {rec[key]!r} is not one of {props[key]['enum']}")
    for i, tok in enumerate(rec.get("tokens") or []):
        where = f"tokens/{i}"
        errors += [f"{where}: '{k}' is a required property" for k in token["required"] if k not in tok]
        if "id" in tok and not id_re.match(str(tok["id"])):
            errors.append(f"{where}/id: {tok['id']!r} does not match the token id pattern")
        if "txt" in tok and not txt_re.match(str(tok["txt"])):
            errors.append(f"{where}/txt: {tok['txt']!r} is empty or contains whitespace")
        if ("t" in tok) != ("t_src" in tok):
            errors.append(f"{where}: 't' and 't_src' must be present together")
        for key, size in (("box", 4), ("t", 2)):
            if key in tok and not (isinstance(tok[key], list) and len(tok[key]) == size):
                errors.append(f"{where}/{key}: {tok[key]!r} must be a list of {size} numbers")
    return errors


def validate(rec: dict) -> list[str]:
    """Human-readable problems with `rec`; empty when it is valid."""
    validator = _jsonschema_validator()
    if validator is None:
        return _fallback_errors(rec)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
            for e in sorted(validator.iter_errors(rec), key=lambda e: list(map(str, e.path)))]


def read_record(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_record(path: Path, rec: dict) -> None:
    """Validate, then write atomically in the format every writer has always used."""
    path = Path(path)
    mode = os.environ.get("PAGE_RECORD_VALIDATION", "warn")
    if mode != "off":
        errors = validate(rec)
        if errors:
            summary = f"invalid page record {path.name}: " + "; ".join(errors[:5])
            if mode == "strict":
                raise RecordValidationError(summary)
            print(f"  WARNING {summary}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def audit(roots: list[Path]) -> dict:
    """Validate every page record under `roots`; print the invalid ones. Read-only."""
    n = invalid = 0
    for root in roots:
        for path in sorted(Path(root).rglob("p*.json")):
            if not PAGE_FILE.match(path.name):
                continue
            n += 1
            errors = validate(read_record(path))
            if errors:
                invalid += 1
                print(f"{path}")
                for e in errors[:10]:
                    print(f"    {e}")
    engine = "jsonschema" if _jsonschema_validator() else "built-in fallback check"
    print(f"\n{n} page record(s) checked ({engine}), {invalid} invalid")
    return {"records": n, "invalid": invalid}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python3 src/pipeline/records.py <work_dir> [<work_dir> ...]")
    result = audit([Path(a) for a in sys.argv[1:]])
    sys.exit(1 if result["invalid"] else 0)
