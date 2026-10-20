"""Spec 009: every page record write is validated against page-record.schema.json.

A typo in a status silently breaks resume (that's how the past resume bugs happened), so
records go through one writer that checks them. Tests run in strict mode (conftest.py);
real runs default to warn so a run is never lost to validation.
"""
import json
import re
from pathlib import Path

import pytest
from conftest import make_token

from pipeline import records

REPO_ROOT = Path(__file__).resolve().parents[2]


def good_record(**overrides):
    rec = {
        "book": "b", "page": 3, "source": "ocr", "ocr_engine": "lighton/parse-v3",
        "box_level": "none", "diac_ratio": 0.0, "path": "diacritise", "status": "extracted",
        "tokens": [make_token("p003w000", "كلمة"), make_token("p003w001", "أخرى")],
    }
    rec.update(overrides)
    return rec


def spoken_token(id_="p003w000", txt="كلمة", t=(0, 400), t_src="anchor"):
    tok = make_token(id_, txt)
    tok.update({"t": list(t), "t_src": t_src})
    return tok


class TestValidate:
    def test_good_record_has_no_errors(self):
        assert records.validate(good_record()) == []

    def test_real_shapes_pass(self):
        rec = good_record(status="spoken", tokens=[
            spoken_token(),
            make_token("p003w001", "غير"),  # untimed: no t, no t_src
            {**make_token("p003w002", "قوس", fixes=["kashida"]), "box": [1.5, 2, 30, 4.25]},
        ], bind_report={"tokens": 3, "anchored": 1, "interpolated": 0, "untimed": 2,
                        "monotonic": True})
        assert records.validate(rec) == []

    @pytest.mark.parametrize("status", ["skipped-empty", "skipped-front-matter"])
    def test_statuses_the_tts_drivers_write_are_valid(self, status):
        assert records.validate(good_record(status=status)) == []

    def test_text_layer_record_without_box_level_is_valid(self):
        rec = good_record(source="pdf-text-layer")
        del rec["box_level"]
        assert records.validate(rec) == []

    def test_four_digit_word_index_is_valid(self):
        rec = good_record(tokens=[make_token("p003w1000", "كلمة")])
        assert records.validate(rec) == []

    @pytest.mark.parametrize("bad, fragment", [
        ({"status": "diacritized"}, "status"),           # US spelling: the resume-breaking typo
        ({"status": "failed"}, "status"),                # 008 records failures elsewhere
        ({"path": "skip"}, "path"),
        ({"source": "text-layer"}, "source"),
        ({"tokens": [make_token("p3w0", "كلمة")]}, "id"),
        ({"tokens": [make_token("p003w000", "يَصْرُخُ؟! ")]}, "txt"),  # the real 017 defect
        ({"tokens": [make_token("p003w000", "")]}, "txt"),
        ({"tokens": [{**make_token("p003w000", "كلمة"), "t": [0, 10]}]}, "t_src"),
        ({"tokens": [{**make_token("p003w000", "كلمة"), "t_src": "interp"}]}, "t"),
        ({"tokens": [{**make_token("p003w000", "كلمة"), "box": [1, 2, 3]}]}, "box"),
        ({"diac_ratio": "0.2"}, "diac_ratio"),
    ])
    def test_bad_records_are_caught(self, bad, fragment):
        errors = records.validate(good_record(**bad))
        assert errors, f"{bad} should be invalid"
        assert any(fragment in e for e in errors), errors

    def test_missing_required_field(self):
        rec = good_record()
        del rec["path"]
        assert any("path" in e for e in records.validate(rec))


class TestFallbackValidator:
    """Without jsonschema installed (system python), a smaller check driven by the same
    schema file still catches the resume-breaking mistakes."""

    @pytest.fixture(autouse=True)
    def no_jsonschema(self, monkeypatch):
        monkeypatch.setattr(records, "_jsonschema_validator", lambda: None)

    def test_good_record(self):
        assert records.validate(good_record()) == []

    @pytest.mark.parametrize("bad", [
        {"status": "diacritized"}, {"path": "skip"}, {"source": "x"},
        {"tokens": [make_token("p3w0", "كلمة")]},
        {"tokens": [make_token("p003w000", "a b")]},
    ])
    def test_catches_the_important_mistakes(self, bad):
        assert records.validate(good_record(**bad))

    def test_missing_required_field(self):
        rec = good_record()
        del rec["tokens"]
        assert records.validate(rec)


class TestWriteRecord:
    def test_strict_mode_refuses_and_leaves_the_old_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PAGE_RECORD_VALIDATION", "strict")
        path = tmp_path / "p003.json"
        records.write_record(path, good_record())
        before = path.read_bytes()

        with pytest.raises(records.RecordValidationError, match="status"):
            records.write_record(path, good_record(status="diacritized"))
        assert path.read_bytes() == before

    def test_warn_mode_writes_and_prints(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PAGE_RECORD_VALIDATION", "warn")
        path = tmp_path / "p003.json"
        records.write_record(path, good_record(status="diacritized"))
        assert json.loads(path.read_text(encoding="utf-8"))["status"] == "diacritized"
        assert "invalid page record" in capsys.readouterr().out

    def test_off_mode_does_not_validate(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PAGE_RECORD_VALIDATION", "off")
        records.write_record(tmp_path / "p003.json", good_record(status="nonsense"))
        assert capsys.readouterr().out == ""

    def test_default_mode_is_warn(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("PAGE_RECORD_VALIDATION", raising=False)
        records.write_record(tmp_path / "p003.json", good_record(status="nonsense"))
        assert "invalid page record" in capsys.readouterr().out

    def test_format_matches_the_existing_files(self, tmp_path):
        """Byte-for-byte what every writer used to produce: ensure_ascii=False, indent=1."""
        path = tmp_path / "p003.json"
        rec = good_record()
        records.write_record(path, rec)
        assert path.read_text(encoding="utf-8") == json.dumps(rec, ensure_ascii=False, indent=1)

    def test_write_is_atomic(self, tmp_path):
        path = tmp_path / "p003.json"
        records.write_record(path, good_record())
        assert [p.name for p in tmp_path.iterdir()] == ["p003.json"]

    def test_read_record_round_trips(self, tmp_path):
        path = tmp_path / "p003.json"
        records.write_record(path, good_record())
        assert records.read_record(path) == good_record()


class TestAudit:
    def test_reports_bad_records_by_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PAGE_RECORD_VALIDATION", "off")
        book = tmp_path / "book"
        records.write_record(book / "p001.json", good_record(page=1))
        records.write_record(book / "p002.json", good_record(page=2, status="diacritized"))
        (book / "failures.json").write_text("{}", encoding="utf-8")  # not a page record

        result = records.audit([tmp_path])

        assert result == {"records": 2, "invalid": 1}
        out = capsys.readouterr().out
        assert "p002.json" in out and "p001.json" not in out


class TestSingleWriter:
    """Every page record write goes through records.write_record (FR-002)."""

    WRITE_RE = re.compile(r"write_text\(\s*json\.dumps\(\s*\w*rec\b", re.DOTALL)

    def test_no_direct_page_record_writes(self):
        offenders = []
        for path in [*(REPO_ROOT / "src/pipeline").glob("*.py"), *(REPO_ROOT / "tools").glob("*.py")]:
            if path.name == "records.py":
                continue  # the one writer
            if self.WRITE_RE.search(path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        assert offenders == [], f"write page records with records.write_record: {offenders}"


def test_schema_runtime_copy_matches_the_spec_contract():
    runtime = REPO_ROOT / "src/pipeline/page-record.schema.json"
    contract = REPO_ROOT / "specs/001-book-pipeline/contracts/page-record.schema.json"
    assert runtime.read_bytes() == contract.read_bytes()
