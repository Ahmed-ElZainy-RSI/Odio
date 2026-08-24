"""Functional tests for tools/build_context_review.py: the diff/case-ending
classifier and the end-to-end HTML build."""
import json

from conftest import make_token, write_page

from build_context_review import classify, find_image, run


class TestClassify:
    def test_diacritic_only_is_case(self):
        assert classify("حكاياتِ", "حكايات") == "case"

    def test_identical_is_case(self):
        assert classify("كتاب", "كتاب") == "case"

    def test_different_letters_is_diff(self):
        """The real page-2 finding: the OCR word and the model's own
        (in that case wrong) proposal are genuinely different letters."""
        assert classify("الْغِنَاءُ", "الْغَابَةِ") == "diff"


class TestFindImage:
    def test_prefers_own_image_over_gemini(self, gemini_dir, lighton_dir):
        (lighton_dir / "pages").mkdir()
        (lighton_dir / "pages" / "p001.jpg").write_bytes(b"fake")
        (gemini_dir / "pages" / "p001.jpg").write_bytes(b"fake")
        result = find_image(lighton_dir, gemini_dir, 1)
        assert result == "pages/p001.jpg"

    def test_falls_back_to_gemini_image(self, gemini_dir, lighton_dir):
        (gemini_dir / "pages" / "p001.jpg").write_bytes(b"fake")
        result = find_image(lighton_dir, gemini_dir, 1)
        assert "book/pages/p001.jpg" in result or result.endswith("pages/p001.jpg")

    def test_no_image_anywhere_returns_none(self, gemini_dir, lighton_dir):
        assert find_image(lighton_dir, gemini_dir, 1) is None


class TestRunEndToEnd:
    def test_builds_html_with_correct_tags_and_decisions(self, gemini_dir, lighton_dir):
        write_page(gemini_dir, 1, [make_token("p001w000", "لماذا")])
        (lighton_dir / "ensemble_review.json").write_text(
            json.dumps({"gemini_dir": str(gemini_dir), "lighton_dir": str(lighton_dir), "pages": {}}),
            encoding="utf-8")
        context_review = {
            "lighton_dir": str(lighton_dir),
            "pages": {"1": [
                {"id": "p001w000", "ocr_word": "الْغِنَاءُ", "proposed": "الْغَابَةِ", "suspicion": 0.96},
                {"id": "p001w001", "ocr_word": "حكاياتِ", "proposed": "حكايات", "suspicion": 0.6},
            ]},
        }
        review_path = lighton_dir / "context_review.json"
        review_path.write_text(json.dumps(context_review, ensure_ascii=False), encoding="utf-8")
        (lighton_dir / "context_decisions.json").write_text(
            json.dumps({"p001w000": "accept"}), encoding="utf-8")

        out = run(review_path)

        html = out.read_text(encoding="utf-8")
        assert 'class="tag diff"' in html
        assert 'class="tag case"' in html
        assert 'class="dec accept"' in html
        assert 'class="dec reject"' in html  # p001w001 defaults to reject
        assert "p001w000" in html
        assert "0.96" in html
