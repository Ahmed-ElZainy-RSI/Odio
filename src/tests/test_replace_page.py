"""Functional tests for tools/replace_page.py: threshold selection and the
wholesale token/box/provenance swap."""
import json

from conftest import make_token, read_page, write_page

from replace_page import find_candidates, replace_page, run


def review_page(gemini_only_count: int, real_count: int) -> list[dict]:
    entries = [{"id": f"gemini-only:p001w{i:03d}", "lighton_txt": None, "action": "FLAG"}
               for i in range(gemini_only_count)]
    entries += [{"id": f"p001w{i:03d}", "lighton_txt": "x", "action": "FLAG"}
                for i in range(gemini_only_count, gemini_only_count + real_count)]
    return entries


class TestFindCandidates:
    def test_page_above_threshold_selected(self):
        review = {"pages": {"1": review_page(gemini_only_count=6, real_count=4)}}  # 60%
        assert find_candidates(review, 0.5) == [1]

    def test_page_below_threshold_not_selected(self):
        review = {"pages": {"1": review_page(gemini_only_count=4, real_count=6)}}  # 40%
        assert find_candidates(review, 0.5) == []

    def test_page_exactly_at_threshold_is_inclusive(self):
        review = {"pages": {"1": review_page(gemini_only_count=5, real_count=5)}}  # 50%
        assert find_candidates(review, 0.5) == [1]

    def test_empty_page_skipped_no_crash(self):
        review = {"pages": {"1": [], "2": review_page(6, 4)}}
        assert find_candidates(review, 0.5) == [2]

    def test_multiple_qualifying_pages_sorted(self):
        review = {"pages": {
            "3": review_page(9, 1), "1": review_page(8, 2), "2": review_page(1, 9),
        }}
        assert find_candidates(review, 0.5) == [1, 3]


class TestReplacePage:
    def test_swaps_tokens_box_level_and_provenance(self, gemini_dir, lighton_dir):
        write_page(gemini_dir, 16, [
            make_token("p016w000", "لماذا", box=[1, 2, 3, 4]),
            make_token("p016w001", "اتصلت", box=[5, 6, 7, 8]),
        ], box_level="line", ocr_engine="gemini-3.6-flash")
        write_page(lighton_dir, 16, [
            make_token("p016w000", "جَ"),
        ], box_level="none", ocr_engine="lighton/parse-v3", status="spoken")

        ok = replace_page(lighton_dir, gemini_dir, 16)

        assert ok is True
        rec = read_page(lighton_dir, 16)
        assert len(rec["tokens"]) == 2
        assert rec["tokens"][0]["id"] == "p016w000"
        assert rec["tokens"][0]["txt"] == "لماذا"
        assert rec["tokens"][0]["box"] == [1, 2, 3, 4]
        assert rec["box_level"] == "line"
        assert rec["ocr_engine"] == "gemini-3.6-flash"
        assert rec["ocr_engine_replaced_from"] == "lighton/parse-v3"
        assert rec["status"] == "extracted"

    def test_missing_gemini_page_skips_without_crashing(self, gemini_dir, lighton_dir):
        write_page(lighton_dir, 16, [make_token("p016w000", "جَ")], status="spoken")
        ok = replace_page(lighton_dir, gemini_dir, 16)
        assert ok is False
        rec = read_page(lighton_dir, 16)
        assert rec["status"] == "spoken"  # untouched


class TestRun:
    def test_no_qualifying_pages_is_a_no_op(self, gemini_dir, lighton_dir, capsys):
        write_page(gemini_dir, 1, [make_token("p001w000", "a")])
        write_page(lighton_dir, 1, [make_token("p001w000", "a")], status="spoken")
        review = {"gemini_dir": str(gemini_dir), "lighton_dir": str(lighton_dir),
                   "pages": {"1": review_page(1, 9)}}
        review_path = lighton_dir / "ensemble_review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")

        run(review_path, 0.5, None)

        rec = read_page(lighton_dir, 1)
        assert rec["status"] == "spoken"  # never touched
        assert "no pages at or above 50%" in capsys.readouterr().out

    def test_explicit_page_overrides_threshold(self, gemini_dir, lighton_dir):
        write_page(gemini_dir, 5, [make_token("p005w000", "x")])
        write_page(lighton_dir, 5, [make_token("p005w000", "y")], status="spoken")
        review = {"gemini_dir": str(gemini_dir), "lighton_dir": str(lighton_dir),
                   "pages": {"5": review_page(1, 9)}}  # only 10% gemini-only
        review_path = lighton_dir / "ensemble_review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")

        run(review_path, 0.5, 5)  # explicit --page 5 bypasses the threshold

        rec = read_page(lighton_dir, 5)
        assert rec["tokens"][0]["txt"] == "x"
        assert rec["status"] == "extracted"
