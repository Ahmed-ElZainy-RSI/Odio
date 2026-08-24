"""Functional tests for tools/ensemble_review.py: normalization, alignment,
page discovery, and the resumability fix (never clobber existing verdicts)."""
import json

import pytest
from conftest import make_token, write_page, write_review

from ensemble_review import align_page, find_pages, normalize_for_compare, run


# ---------------------------------------------------------------------------
# normalize_for_compare
# ---------------------------------------------------------------------------

class TestNormalizeForCompare:
    def test_diacritics_stripped(self):
        assert normalize_for_compare("الْغَابَةِ") == normalize_for_compare("الغابة")

    def test_ta_marbuta_folded(self):
        assert normalize_for_compare("الغابة").endswith("ه")

    def test_alif_forms_folded(self):
        assert normalize_for_compare("أحمد") == normalize_for_compare("احمد")
        assert normalize_for_compare("إحمد") == normalize_for_compare("احمد")
        assert normalize_for_compare("آحمد") == normalize_for_compare("احمد")

    def test_alif_maksura_folded(self):
        assert normalize_for_compare("مصطفى") == normalize_for_compare("مصطفي")

    def test_tatweel_removed(self):
        assert normalize_for_compare("العنـوان") == normalize_for_compare("العنوان")

    def test_trailing_punctuation_ignored(self):
        """Regression: the pilot book's first pass flagged 22/244 tokens as
        false disagreements purely from trailing punctuation before this was
        fixed (comma, markdown emphasis)."""
        assert normalize_for_compare("مِصْرَ،") == normalize_for_compare("مِصْرَ")
        assert normalize_for_compare("كتاب**") == normalize_for_compare("كتاب")

    def test_genuinely_different_words_stay_different(self):
        """Regression for the real page-2 finding on ثعلوب المكار: الْغِنَاءُ
        ("singing") and الْغَنَّاءِ ("lush") are NOT the same word after
        normalization, even though they look similar undiacritized."""
        assert normalize_for_compare("الْغِنَاءُ") != normalize_for_compare("الْغَابَةِ")


# ---------------------------------------------------------------------------
# align_page
# ---------------------------------------------------------------------------

class TestAlignPage:
    def _tokens(self, *words, prefix="p001w"):
        return [make_token(f"{prefix}{i:03d}", w) for i, w in enumerate(words)]

    def test_identical_tokens_all_keep(self):
        gemini = self._tokens("كان", "في", "الغابة")
        lighton = self._tokens("كان", "في", "الغابة")
        results = align_page(gemini, lighton)
        assert len(results) == 3
        assert all(r["action"] == "KEEP" and r["agree"] for r in results)
        assert [r["gemini_id"] for r in results] == [t["id"] for t in gemini]
        assert [r["id"] for r in results] == [t["id"] for t in lighton]

    def test_one_word_disagreement_flags_only_that_word(self):
        gemini = self._tokens("كان", "في", "الغابة")
        lighton = self._tokens("كان", "في", "الغِناء")
        results = align_page(gemini, lighton)
        actions = [r["action"] for r in results]
        assert actions == ["KEEP", "KEEP", "FLAG"]
        assert results[2]["lighton_txt"] == "الغِناء"
        assert results[2]["gemini_txt"] == "الغابة"

    def test_diacritic_only_difference_is_keep_not_flag(self):
        gemini = self._tokens("كَانَ")
        lighton = self._tokens("كان")
        results = align_page(gemini, lighton)
        assert results[0]["action"] == "KEEP"

    def test_punctuation_only_difference_is_keep_not_flag(self):
        gemini = self._tokens("مِصْرَ")
        lighton = self._tokens("مِصْرَ،")
        results = align_page(gemini, lighton)
        assert results[0]["action"] == "KEEP"

    def test_gemini_has_extra_word_produces_gemini_only_entry(self):
        gemini = self._tokens("كان", "في", "الغابة")
        lighton = self._tokens("كان", "الغابة")
        results = align_page(gemini, lighton)
        gemini_only = [r for r in results if r["id"].startswith("gemini-only:")]
        assert len(gemini_only) == 1
        assert gemini_only[0]["lighton_txt"] is None
        assert gemini_only[0]["gemini_txt"] == "في"

    def test_lighton_has_extra_word_produces_gemini_txt_none(self):
        gemini = self._tokens("كان", "الغابة")
        lighton = self._tokens("كان", "جداً", "الغابة")
        results = align_page(gemini, lighton)
        extra = [r for r in results if r.get("gemini_txt") is None and not r["id"].startswith("gemini-only:")]
        assert len(extra) == 1
        assert extra[0]["lighton_txt"] == "جداً"


# ---------------------------------------------------------------------------
# find_pages
# ---------------------------------------------------------------------------

def test_find_pages_only_matches_pnnn_json(tmp_path):
    write_page(tmp_path, 1, [make_token("p001w000", "a")])
    write_page(tmp_path, 2, [make_token("p002w000", "b")])
    (tmp_path / "notes.txt").write_text("not a page")
    pages = find_pages(tmp_path)
    assert set(pages.keys()) == {1, 2}


# ---------------------------------------------------------------------------
# run() -- the resumability fix
# ---------------------------------------------------------------------------

def test_run_extends_without_clobbering_existing_verdicts(gemini_dir, lighton_dir):
    write_page(gemini_dir, 1, [make_token("p001w000", "الغابة")])
    write_page(gemini_dir, 2, [make_token("p002w000", "كتاب")])
    write_page(lighton_dir, 1, [make_token("p001w000", "الغابة")])
    write_page(lighton_dir, 2, [make_token("p002w000", "كتاب")])

    # simulate vlm_arbiter.py having already run on page 1
    existing_entry = {
        "id": "p001w000", "gemini_id": "p001w000", "lighton_txt": "الغِناء",
        "gemini_txt": "الغابة", "agree": False, "action": "VERIFIED",
        "verified_txt": "الْغَنَّاءِ",
    }
    write_review(lighton_dir, gemini_dir, {"1": [existing_entry]})

    run(gemini_dir, lighton_dir)

    result = json.loads((lighton_dir / "ensemble_review.json").read_text(encoding="utf-8"))
    assert result["pages"]["1"] == [existing_entry], "page 1's VERIFIED verdict must survive untouched"
    assert "2" in result["pages"], "page 2 should be newly compared"


def test_run_raises_if_no_overlapping_pages(gemini_dir, lighton_dir):
    write_page(gemini_dir, 1, [make_token("p001w000", "a")])
    write_page(lighton_dir, 2, [make_token("p002w000", "b")])
    with pytest.raises(SystemExit):
        run(gemini_dir, lighton_dir)
