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


def verified(id_, verified_txt, lighton_txt=None, gemini_txt=None):
    """A VERIFIED entry as vlm_arbiter.py leaves it. Real entries always carry
    both engines' reads (ensemble_review.py writes them); by default the VLM
    sided with Gemini -- the common real case -- so the verdict is applied."""
    return {"id": id_, "action": "VERIFIED", "verified_txt": verified_txt,
            "lighton_txt": lighton_txt,
            "gemini_txt": verified_txt if gemini_txt is None else gemini_txt}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


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


class TestThirdAnswerRouting:
    """Spec 017: a VLM verdict that neither engine produced is a model inventing
    text. It is never applied automatically -- it goes to the human review queue
    (vlm_review.json + context_decisions.json, default "reject")."""

    def test_answer_neither_engine_read_is_not_applied(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغناء")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "الغابة", lighton_txt="الغناء", gemini_txt="الغنا")]})

        run(lighton_dir / "ensemble_review.json")

        rec = read_page(lighton_dir, 1)
        assert rec["tokens"][0]["txt"] == "الغناء"
        assert "ocr_txt" not in rec["tokens"][0]
        assert rec["status"] == "spoken"

        queued = read_json(lighton_dir / "vlm_review.json")["pages"]["1"]
        assert queued == [{
            "id": "p001w000", "ocr_word": "الغناء", "proposed": "الغابة", "suspicion": 1.0,
            "source": "vlm-third-answer", "lighton_txt": "الغناء", "gemini_txt": "الغنا",
        }]
        assert read_json(lighton_dir / "context_decisions.json") == {"p001w000": "reject"}

    def test_isbn_digit_reguess_is_not_applied(self, gemini_dir, lighton_dir):
        """The real case from the pilot book: the VLM re-guessed an ISBN digit
        string differently from both engines."""
        write_page(lighton_dir, 1, [make_token("p001w000", "978-977-1")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "978-977-4", lighton_txt="978-977-1", gemini_txt="978-977-7")]})
        run(lighton_dir / "ensemble_review.json")
        assert read_page(lighton_dir, 1)["tokens"][0]["txt"] == "978-977-1"

    def test_agreement_with_lighton_after_punctuation_folding_is_applied(
            self, gemini_dir, lighton_dir):
        """The VLM reading LightOn's letters with different punctuation agrees
        with LightOn -- nothing was invented, so the verdict is applied."""
        write_page(lighton_dir, 1, [make_token("p001w000", "قزح")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "قُزَحَ،", lighton_txt="قزح", gemini_txt="فزح")]})
        run(lighton_dir / "ensemble_review.json")
        # letters equal the token's -> no-op, exactly as before this feature
        assert read_page(lighton_dir, 1)["status"] == "spoken"

    def test_dropped_digit_is_not_agreement(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "صفحة5")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "صفحة", lighton_txt="صفحة5", gemini_txt="صفحة6")]})
        run(lighton_dir / "ensemble_review.json")
        assert read_page(lighton_dir, 1)["tokens"][0]["txt"] == "صفحة5"

    def test_trailing_whitespace_in_verdict_is_stripped(self, gemini_dir, lighton_dir):
        """FR-005: the real defect -- 'يَصْرُخُ؟! ' was written into a token with
        its trailing space, which tashkel.py's join/split can never round-trip."""
        write_page(lighton_dir, 1, [make_token("p001w000", "يصرح؟!")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "يَصْرُخُ؟! ", lighton_txt="يصرح؟!", gemini_txt="يَصْرُخُ؟!")]})
        run(lighton_dir / "ensemble_review.json")
        assert read_page(lighton_dir, 1)["tokens"][0]["txt"] == "يَصْرُخُ؟!"

    def test_multi_word_verdict_for_one_token_goes_to_review(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "قوس")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "قوس قزح", lighton_txt="قوس", gemini_txt="قرح")]})
        run(lighton_dir / "ensemble_review.json")
        assert read_page(lighton_dir, 1)["tokens"][0]["txt"] == "قوس"
        queued = read_json(lighton_dir / "vlm_review.json")["pages"]["1"]
        assert queued[0]["source"] == "vlm-multi-word"

    def test_rerun_keeps_a_reviewers_decision(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغناء")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "الغابة", lighton_txt="الغناء", gemini_txt="الغنا")]})
        run(lighton_dir / "ensemble_review.json")
        (lighton_dir / "context_decisions.json").write_text(
            json.dumps({"p001w000": "accept"}), encoding="utf-8")

        run(lighton_dir / "ensemble_review.json")

        assert read_json(lighton_dir / "context_decisions.json") == {"p001w000": "accept"}
        assert len(read_json(lighton_dir / "vlm_review.json")["pages"]["1"]) == 1

    def test_existing_context_decisions_are_merged_not_replaced(self, gemini_dir, lighton_dir):
        (lighton_dir / "context_decisions.json").write_text(
            json.dumps({"p001w009": "accept"}), encoding="utf-8")
        write_page(lighton_dir, 1, [make_token("p001w000", "الغناء")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "الغابة", lighton_txt="الغناء", gemini_txt="الغنا")]})
        run(lighton_dir / "ensemble_review.json")
        assert read_json(lighton_dir / "context_decisions.json") == {
            "p001w009": "accept", "p001w000": "reject"}

    def test_no_queue_file_when_nothing_routed(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغِناء")], status="spoken")
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الغابة")]})
        run(lighton_dir / "ensemble_review.json")
        assert not (lighton_dir / "vlm_review.json").exists()
        assert not (lighton_dir / "context_decisions.json").exists()


class TestClassifyVerdict:
    def test_labels(self):
        from apply_corrections import classify_verdict
        entry = {"lighton_txt": "الغناء", "gemini_txt": "الغنا"}
        assert classify_verdict(entry, "الْغِنَاءُ") == "match-lighton"
        assert classify_verdict(entry, "الغنا") == "match-gemini"
        assert classify_verdict(entry, "«الغناء»!") == "match-lighton"  # punctuation folded
        assert classify_verdict({"lighton_txt": "صفحة5", "gemini_txt": None}, "صفحة") == \
            "third-answer"  # a dropped digit is not agreement
        assert classify_verdict(entry, "الغابة") == "third-answer"
        assert classify_verdict(entry, "الغابة الكبيرة") == "multi-word"
        assert classify_verdict({"lighton_txt": None, "gemini_txt": None}, "شيء") == "third-answer"
