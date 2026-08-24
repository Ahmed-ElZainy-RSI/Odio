"""Functional + behavioral tests for tools/apply_corrections.py.

The diacritic-tolerance test here (test_diacritic_only_verdict_not_reapplied)
is a direct regression test for a real bug: this tool used to compare
corrected text against a VLM verdict with exact string equality, and
vlm_arbiter.py often returns undiacritized text even when the token was
already correctly diacritized -- so a second run would strip diacritics
back off already-correct words and force a pointless re-diacritize/re-TTS
cycle. See README.md's Open Items and TESTING.md section 3.6.

Note on the word pairs used below: الْغِنَاءُ ("singing") and الْغَنَّاءِ ("lush",
the real word from TESTING.md's ثعلوب المكار page-2 finding) share the exact
same base letters -- غ ن ا ء -- and differ only by a shadda, which
normalize_for_compare() correctly folds away as a diacritic. So they are NOT
a valid "genuinely different letters" test case (an earlier draft of this
file assumed they were, and the tests failed against the real code, not the
other way around). الْغَابَةِ ("forest") is used instead where the tests need
letters that actually differ.
"""
import json

from apply_corrections import run
from conftest import make_token, read_page, write_page, write_review


def verified(id_, verified_txt):
    return {"id": id_, "action": "VERIFIED", "verified_txt": verified_txt}


class TestOrdinarySubstitution:
    def test_applies_correction_and_preserves_original(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الغابة")]})

        run(lighton_dir / "ensemble_review.json")

        rec = read_page(lighton_dir, 1)
        tok = rec["tokens"][0]
        assert tok["txt"] == "الغابة"
        assert tok["ocr_txt"] == "الغِناء"
        assert "ensemble-vlm" in tok["fixes"]
        assert rec["status"] == "extracted"

    def test_no_op_when_nothing_verified(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "كتاب")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            {"id": "p001w000", "action": "KEEP", "agree": True},
        ]})
        run(lighton_dir / "ensemble_review.json")
        rec = read_page(lighton_dir, 1)
        assert rec["status"] == "spoken"  # untouched
        assert "ocr_txt" not in rec["tokens"][0]


class TestDiacriticTolerance:
    def test_diacritic_only_verdict_not_reapplied(self, gemini_dir, lighton_dir):
        """verified_txt is undiacritized ('حكايات'); the token already has
        correct diacritics ('حكاياتِ') from an earlier run_tashkel pass.
        This must be a no-op, not a diacritic-stripping 'correction'."""
        write_page(lighton_dir, 1, [make_token("p001w000", "حكاياتِ")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "حكايات")]})

        run(lighton_dir / "ensemble_review.json")

        rec = read_page(lighton_dir, 1)
        tok = rec["tokens"][0]
        assert tok["txt"] == "حكاياتِ", "diacritics must not be stripped back off"
        assert "ocr_txt" not in tok
        assert rec["status"] == "spoken", "status must not reset when nothing actually changed"

    def test_genuinely_different_letters_still_correct(self, gemini_dir, lighton_dir):
        """Sanity check the tolerance isn't so loose it hides real corrections."""
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناءُ")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الْغَابَةِ")]})
        run(lighton_dir / "ensemble_review.json")
        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "الْغَابَةِ"
        assert rec["status"] == "extracted"


class TestDeletion:
    def test_empty_verdict_deletes_token(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [
            make_token("p001w000", "كان"),
            make_token("p001w001", "نَعْدُّ"),
            make_token("p001w002", "جميلاً"),
        ], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w001", "")]})

        run(lighton_dir / "ensemble_review.json")

        rec = read_page(lighton_dir, 1)
        ids = [t["id"] for t in rec["tokens"]]
        assert "p001w001" not in ids
        assert len(rec["tokens"]) == 2


class TestGeminiOnlyNeverApplied:
    def test_gemini_only_entry_does_not_crash_or_write(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "كان")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            {"id": "gemini-only:p001w005", "action": "VERIFIED", "verified_txt": "شيء"},
        ]})
        run(lighton_dir / "ensemble_review.json")  # must not raise
        rec = read_page(lighton_dir, 1)
        assert rec["status"] == "spoken"
        assert len(rec["tokens"]) == 1


class TestIdempotency:
    def test_rerun_produces_zero_changes(self, gemini_dir, lighton_dir, capsys):
        write_page(lighton_dir, 1, [make_token("p001w000", "حكاياتِ")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "حكايات")]})

        run(lighton_dir / "ensemble_review.json")
        before = json.dumps(read_page(lighton_dir, 1), sort_keys=True)

        run(lighton_dir / "ensemble_review.json")
        after = json.dumps(read_page(lighton_dir, 1), sort_keys=True)

        assert before == after

    def test_rerun_after_real_correction_is_stable(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الغابة")]})

        run(lighton_dir / "ensemble_review.json")
        after_first = read_page(lighton_dir, 1)
        assert after_first["tokens"][0]["fixes"].count("ensemble-vlm") == 1

        run(lighton_dir / "ensemble_review.json")
        after_second = read_page(lighton_dir, 1)
        assert after_second["tokens"][0]["fixes"].count("ensemble-vlm") == 1, \
            "fixes[] must not accumulate duplicate entries on repeated runs"
