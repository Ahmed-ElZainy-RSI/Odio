"""Spec 018: every correction the tools apply carries its evidence.

The page-record schema validates every evidence entry on write (conftest runs strict), so
these tests only need to assert the content; a malformed entry would already fail.
"""
import json
from pathlib import Path

import apply_context_suggestions
import apply_corrections
import replace_page
from conftest import make_token, read_page, write_page, write_review

REPO_ROOT = Path(__file__).resolve().parents[2]


def verified(id_, verdict, lighton, gemini, **extra):
    return {"id": id_, "action": "VERIFIED", "verified_txt": verdict,
            "lighton_txt": lighton, "gemini_txt": gemini, **extra}


class TestApplyCorrections:
    def test_applied_fix_records_its_evidence(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الفضل")])
        write_review(lighton_dir, gemini_dir, {"1": [
            verified("p001w000", "الفصل", "الفضل", "الفصل", vlm_model="gemini-3.6-flash")]})

        apply_corrections.run(lighton_dir / "ensemble_review.json")

        [ev] = read_page(lighton_dir, 1)["tokens"][0]["evidence"]
        assert ev["token_id"] == "p001w000"
        assert (ev["original"], ev["candidate"]) == ("الفضل", "الفصل")
        assert ev["applied"] is True and ev["status"] == "ACCEPT"
        assert (ev["detector"], ev["decided_by"], ev["rule"]) == ("ensemble", "rule", "match-gemini")
        assert ev["reads"] == {"lighton": "الفضل", "gemini": "الفصل", "vlm": "الفصل"}
        assert ev["models"] == {"vlm": "gemini-3.6-flash"}

    def test_older_reviews_without_a_model_still_record_evidence(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الفضل")])
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الفصل", "الفضل", "الفصل")]})
        apply_corrections.run(lighton_dir / "ensemble_review.json")
        [ev] = read_page(lighton_dir, 1)["tokens"][0]["evidence"]
        assert "models" not in ev

    def test_rerun_does_not_duplicate_evidence(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الفضل")])
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الفصل", "الفضل", "الفصل")]})
        apply_corrections.run(lighton_dir / "ensemble_review.json")
        apply_corrections.run(lighton_dir / "ensemble_review.json")
        assert len(read_page(lighton_dir, 1)["tokens"][0]["evidence"]) == 1

    def test_routed_third_answer_writes_no_evidence_on_the_token(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغناء")])
        write_review(lighton_dir, gemini_dir, {"1": [verified("p001w000", "الغابة", "الغناء", "الغنا")]})
        apply_corrections.run(lighton_dir / "ensemble_review.json")
        assert "evidence" not in read_page(lighton_dir, 1)["tokens"][0]


class TestApplyContextSuggestions:
    def write(self, lighton_dir, name, entries, decisions):
        (lighton_dir / name).write_text(json.dumps(
            {"lighton_dir": str(lighton_dir), "pages": {"1": entries}}, ensure_ascii=False),
            encoding="utf-8")
        (lighton_dir / "context_decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

    def test_accepted_context_suggestion(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الْغِنَاءُ")])
        self.write(lighton_dir, "context_review.json", [
            {"id": "p001w000", "ocr_word": "الْغِنَاءُ", "proposed": "الْغَنَّاءِ",
             "suspicion": 0.96, "model": "deepseek/deepseek-v4-pro-0813"}],
            {"p001w000": "accept"})

        apply_context_suggestions.run(lighton_dir / "context_review.json")

        [ev] = read_page(lighton_dir, 1)["tokens"][0]["evidence"]
        assert (ev["detector"], ev["decided_by"], ev["rule"]) == ("context", "human", "human-accept")
        assert ev["reads"] == {"llm": "الْغَنَّاءِ"}
        assert ev["scores"] == {"suspicion": 0.96}
        assert ev["models"] == {"llm": "deepseek/deepseek-v4-pro-0813"}

    def test_custom_text_from_the_reviewer(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغناء")])
        self.write(lighton_dir, "context_review.json", [
            {"id": "p001w000", "ocr_word": "الغناء", "proposed": "الغابة", "suspicion": 0.9}],
            {"p001w000": "الغنّاء"})
        apply_context_suggestions.run(lighton_dir / "context_review.json")
        [ev] = read_page(lighton_dir, 1)["tokens"][0]["evidence"]
        assert ev["rule"] == "human-custom"
        assert ev["candidate"] == "الغنّاء"

    def test_accepted_vlm_third_answer(self, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "الغناء")])
        self.write(lighton_dir, "context_review.json", [], {"p001w000": "accept"})
        (lighton_dir / "vlm_review.json").write_text(json.dumps({"lighton_dir": str(lighton_dir), "pages": {"1": [
            {"id": "p001w000", "ocr_word": "الغناء", "proposed": "الغابة", "suspicion": 1.0,
             "source": "vlm-third-answer", "lighton_txt": "الغناء", "gemini_txt": "الغنا"}]}},
            ensure_ascii=False), encoding="utf-8")

        apply_context_suggestions.run(lighton_dir / "context_review.json")

        [ev] = read_page(lighton_dir, 1)["tokens"][0]["evidence"]
        assert (ev["detector"], ev["decided_by"]) == ("ensemble", "human")
        assert ev["reads"] == {"lighton": "الغناء", "gemini": "الغنا", "vlm": "الغابة"}


class TestReplacePage:
    def test_page_replacement_is_recorded_on_the_page(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 1, [make_token("p001w000", "x")], ocr_engine="lighton/parse-v3",
                   box_level="none")
        write_page(gemini_dir, 1, [make_token("p001w000", "كلمة"), make_token("p001w001", "أخرى")],
                   ocr_engine="gemini-3.6-flash", box_level="line")
        write_review(lighton_dir, gemini_dir, {"1": [
            {"id": "gemini-only:p001w000", "action": "FLAG"},
            {"id": "gemini-only:p001w001", "action": "FLAG"},
            {"id": "p001w000", "action": "FLAG"},
        ]})

        replace_page.run(lighton_dir / "ensemble_review.json", threshold=0.5, only_page=None)

        [ev] = read_page(lighton_dir, 1)["page_evidence"]
        assert ev["rule"] == "gemini-only >= 50%"
        assert ev["gemini_only_ratio"] == round(2 / 3, 3)
        assert (ev["replaced_engine"], ev["replacement_engine"]) == ("lighton/parse-v3", "gemini-3.6-flash")
        assert ev["replaced_tokens"] == 1


def test_pipeline_copy_matches_the_service_domain_module():
    service = REPO_ROOT / "service/ai_engine/domain/evidence.py"
    assert (REPO_ROOT / "src/pipeline/evidence.py").read_bytes() == service.read_bytes()


class TestReaderShowsEvidence:
    def test_latest_evidence_reaches_the_reader(self, tmp_path):
        from pipeline import build_reader

        book = tmp_path / "book"
        tok = make_token("p001w000", "الفصل")
        tok.update({"ocr_txt": "الفضل", "t": [0, 500], "t_src": "anchor", "evidence": [{
            "token_id": "p001w000", "original": "الفضل", "candidate": "الفصل", "applied": True,
            "status": "ACCEPT", "detector": "ensemble", "decided_by": "rule",
            "rule": "match-gemini", "at": "2026-10-21T09:00:00+00:00"}]})
        write_page(book, 1, [tok], audio="audio/p001.wav", duration_ms=500, box_level="none")
        (book / "audio").mkdir()
        (book / "audio" / "p001.wav").write_bytes(b"RIFF")
        (book / "pages").mkdir()
        (book / "pages" / "p001.jpg").write_bytes(b"jpeg")

        html = build_reader.build(book).read_text(encoding="utf-8")

        assert '"e":{"o":"الفضل","x":"ensemble","d":"rule","r":"match-gemini","at":"2026-10-21"}' in html
        assert "corrected from" in html
