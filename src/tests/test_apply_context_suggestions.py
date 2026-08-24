"""Functional tests for tools/apply_context_suggestions.py: the accept/
reject/custom decision handling, staleness detection, and its own
diacritic-tolerance fix (same bug class as apply_corrections.py, fixed the
same way -- see TESTING.md section 4.4 / README's Open Items)."""
import json

import pytest
from conftest import make_token, read_page, write_page

from apply_context_suggestions import run


def write_context_review(lighton_dir, pages: dict) -> None:
    (lighton_dir / "context_review.json").write_text(
        json.dumps({"lighton_dir": str(lighton_dir), "pages": pages}), encoding="utf-8")


def write_decisions(lighton_dir, decisions: dict) -> None:
    (lighton_dir / "context_decisions.json").write_text(json.dumps(decisions), encoding="utf-8")


def suggestion(id_, ocr_word, proposed, suspicion=0.9):
    return {"id": id_, "ocr_word": ocr_word, "proposed": proposed, "suspicion": suspicion}


class TestDecisionHandling:
    def test_accept_applies_proposed_word(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "الغِناء", "الغابة")]})
        write_decisions(lighton_dir, {"p001w000": "accept"})

        run(lighton_dir / "context_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "الغابة"
        assert rec["tokens"][0]["ocr_txt"] == "الغِناء"
        assert "context-llm" in rec["tokens"][0]["fixes"]
        assert rec["status"] == "extracted"

    def test_default_reject_is_not_applied(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "الغِناء", "الغابة")]})
        write_decisions(lighton_dir, {"p001w000": "reject"})

        run(lighton_dir / "context_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "الغِناء"
        assert rec["status"] == "spoken"

    def test_id_absent_from_decisions_defaults_to_reject(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "الغِناء", "الغابة")]})
        write_decisions(lighton_dir, {})  # id never decided on

        run(lighton_dir / "context_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "الغِناء"

    def test_custom_string_used_verbatim(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "الغِناء", "الغابة")]})
        write_decisions(lighton_dir, {"p001w000": "المكتبة"})  # reviewer's own correction

        run(lighton_dir / "context_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "المكتبة"


class TestStaleness:
    def test_letters_changed_since_detection_is_skipped(self, lighton_dir):
        """Something else corrected this token since context_detector.py ran
        -- the ocr_word snapshot no longer matches. Must not overwrite."""
        write_page(lighton_dir, 1, [make_token("p001w000", "شيء_آخر")], status="spoken")
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "الغِناء", "الغابة")]})
        write_decisions(lighton_dir, {"p001w000": "accept"})

        run(lighton_dir / "context_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "شيء_آخر"  # untouched
        assert rec["status"] == "spoken"

    def test_diacritic_only_drift_is_not_treated_as_stale(self, lighton_dir):
        """Regression: a neighboring correction's re-diacritization pass can
        shift this token's diacritics without changing its letters -- that
        must still be treated as the same word, not 'changed since
        detection'."""
        write_page(lighton_dir, 1, [make_token("p001w000", "حكاياتِ")], status="spoken")
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "حكايات", "قصص")]})
        write_decisions(lighton_dir, {"p001w000": "accept"})

        run(lighton_dir / "context_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "قصص"
        assert rec["status"] == "extracted"


class TestMissingDecisionsFile:
    def test_raises_if_no_decisions_file(self, lighton_dir):
        write_context_review(lighton_dir, {"1": [suggestion("p001w000", "a", "b")]})
        with pytest.raises(SystemExit):
            run(lighton_dir / "context_review.json")
