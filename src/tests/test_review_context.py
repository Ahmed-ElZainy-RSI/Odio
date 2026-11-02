"""Spec 034: every proposed change carries its phrase, and a person decides in the page --
including on the image-verified fixes apply_corrections.py applies by itself."""
import http.client
import json
import threading

import pytest
from conftest import make_token, read_page, write_page, write_review

import apply_context_suggestions
import build_context_review
import context_detector
import ensemble_review
from ensemble_review import add_context, phrase_around


def row(id_, lighton, gemini=None, action="KEEP", **extra):
    return {"id": id_, "lighton_txt": lighton, "gemini_txt": gemini or lighton,
            "agree": action == "KEEP", "action": action, **extra}


SENTENCE = ["قال", "الجد", "وهو", "يعد", "كوبا", "من", "الشاي.", "ضحك", "إبراهيم"]


def rows_of(words, flagged=()):
    return [row(f"p001w{i:03d}", w, action="FLAG" if i in flagged else "KEEP")
            for i, w in enumerate(words)]


# ── the phrase ───────────────────────────────────────────────────────────────

class TestPhraseAround:
    def test_words_on_both_sides_up_to_the_sentence_end(self):
        assert phrase_around(rows_of(SENTENCE), 3) == {
            "before": "قال الجد وهو", "after": "كوبا من الشاي."}

    def test_never_crosses_into_the_previous_sentence(self):
        assert phrase_around(rows_of(SENTENCE), 7) == {"before": "", "after": "إبراهيم"}

    def test_a_sentence_final_word_has_nothing_after(self):
        assert phrase_around(rows_of(SENTENCE), 6)["after"] == ""

    def test_at_most_span_words_each_side(self):
        words = [f"كلمة{i}" for i in range(20)]
        p = phrase_around(rows_of(words), 10, span=3)
        assert p == {"before": "كلمة7 كلمة8 كلمة9", "after": "كلمة11 كلمة12 كلمة13"}

    def test_gemini_only_row_gets_the_text_around_its_place(self):
        rows = rows_of(["قال", "الجد"]) + [row("gemini-only:p001w002", "", "وهو", "FLAG")] \
            + rows_of(["يعد"])
        assert phrase_around(rows, 2) == {"before": "قال الجد", "after": "يعد"}


class TestEnsembleReviewContext:
    def test_only_reviewable_rows_get_a_phrase(self):
        rows = rows_of(SENTENCE, flagged={3})
        assert add_context(rows) == 1
        assert rows[3]["context"]["before"] == "قال الجد وهو"
        assert "context" not in rows[0]

    def test_an_old_report_gains_phrases_without_losing_verdicts(self, gemini_dir, lighton_dir):
        write_page(gemini_dir, 1, [make_token("p001w000", "الجد")], ocr_engine="gemini-3.6-flash")
        write_page(lighton_dir, 1, [make_token("p001w000", "الجُد")], ocr_engine="lighton/parse-v3")
        old = [row("p001w000", "قال", action="KEEP"),
               row("p001w001", "الجُد", "الجد", "VERIFIED", verified_txt="الجد", vlm_model="m")]
        write_review(lighton_dir, gemini_dir, {"1": old})

        ensemble_review.run(gemini_dir, lighton_dir)

        rows = json.loads((lighton_dir / "ensemble_review.json").read_text(encoding="utf-8"))["pages"]["1"]
        assert rows[1]["verified_txt"] == "الجد" and rows[1]["action"] == "VERIFIED"
        assert rows[1]["context"] == {"before": "قال", "after": ""}


class TestContextDetectorContext:
    def test_new_suggestions_carry_their_phrase(self, lighton_dir, gemini_dir, monkeypatch):
        review = write_review(lighton_dir, gemini_dir, {"1": rows_of(SENTENCE, flagged={2})})
        monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
        monkeypatch.setattr(context_detector, "detect_page",
                            lambda key, entries: [{"id": "p001w003", "suspicion": 0.9, "proposed": "يُعِدّ"}])

        out = context_detector.run(review)

        entry = json.loads(out.read_text(encoding="utf-8"))["pages"]["1"][0]
        # the flagged word (index 2) is in the phrase even though the model was not sent it
        assert entry["context"] == {"before": "قال الجد وهو", "after": "كوبا من الشاي."}

    def test_old_suggestions_are_backfilled_without_a_call(self, lighton_dir, gemini_dir, monkeypatch):
        review = write_review(lighton_dir, gemini_dir, {"1": rows_of(SENTENCE)})
        (lighton_dir / "context_review.json").write_text(json.dumps({
            "lighton_dir": str(lighton_dir),
            "pages": {"1": [{"id": "p001w001", "ocr_word": "الجد", "suspicion": 0.8, "proposed": "الجدّ"}]},
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        def no_call(*a, **k):
            raise AssertionError("a backfill must not call the model")
        monkeypatch.setattr(context_detector, "detect_page", no_call)

        out = context_detector.run(review)

        entry = json.loads(out.read_text(encoding="utf-8"))["pages"]["1"][0]
        assert entry["context"] == {"before": "قال", "after": "وهو يعد كوبا من الشاي."}


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.fixture
def book(gemini_dir, lighton_dir):
    """One page: an LLM suggestion on w001 and an applied image fix on w003."""
    rows = rows_of(SENTENCE)
    rows[3] = row("p001w003", "يعذ", "يعد", "VERIFIED", verified_txt="يُعِدُّ", vlm_model="gemini-x")
    write_review(lighton_dir, gemini_dir, {"1": rows})
    (lighton_dir / "context_review.json").write_text(json.dumps({
        "lighton_dir": str(lighton_dir),
        "pages": {"1": [{"id": "p001w001", "ocr_word": "الجد", "suspicion": 0.8,
                         "proposed": "الحد", "model": "deepseek"}]},
    }, ensure_ascii=False), encoding="utf-8")
    tokens = [make_token(f"p001w{i:03d}", w) for i, w in enumerate(SENTENCE)]
    tokens[3].update({"txt": "يُعِدُّ", "ocr_txt": "يعذ", "fixes": ["ensemble-vlm"]})
    write_page(lighton_dir, 1, tokens, status="spoken")
    (lighton_dir / "context_decisions.json").write_text(json.dumps({"p001w001": "reject"}), encoding="utf-8")
    return lighton_dir


class TestReviewPage:
    def test_every_entry_shows_its_phrase_readings_and_controls(self, book):
        html = build_context_review.run(book / "context_review.json").read_text(encoding="utf-8")

        assert '<span class=ctx>قال</span> <mark>الجد</mark>' in html
        assert "Applied automatically from the page image (1)" in html
        assert "LLM suggestions (both engines agreed) (1)" in html
        assert "Undo fix, back to <b>يعذ</b>" in html and "Keep fix <b>يُعِدُّ</b>" in html
        assert "Keep OCR word <b>الجد</b>" in html and "Use proposal <b>الحد</b>" in html
        assert "image verdict</small> <b>يُعِدُّ</b>" in html

    def test_defaults_are_reject_for_suggestions_and_accept_for_fixes(self, book):
        html = build_context_review.run(book / "context_review.json").read_text(encoding="utf-8")
        data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])

        assert data["decisions"] == {"p001w001": "reject", "p001w003": "accept"}

    def test_page_text_is_escaped(self, book):
        (book / "context_decisions.json").write_text(json.dumps({"p001w001": "<b>x</b>"}), encoding="utf-8")
        html = build_context_review.run(book / "context_review.json").read_text(encoding="utf-8")
        assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html.split("<script")[0]


# ── saving from the page ─────────────────────────────────────────────────────

@pytest.fixture
def server(book):
    srv, url = build_context_review.make_server(book / "context_review.json", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


class TestServer:
    def test_serves_the_page_and_current_decisions(self, server):
        srv, url = server
        path = url.split(str(srv.server_address[1]), 1)[1]
        status, page = call(srv, "GET", path)
        assert status == 200 and "مراجعة التصحيحات".encode() in page
        status, body = call(srv, "GET", "/book-lighton/api/decisions")
        assert json.loads(body) == {"p001w001": "reject"}

    def test_a_saved_decision_is_applied(self, server, book):
        srv, _ = server
        status, body = call(srv, "POST", "/book-lighton/api/decisions",
                            json.dumps({"p001w001": "accept", "p001w003": "reject"}))
        assert status == 200 and json.loads(body) == {"saved": 2}

        apply_context_suggestions.run(book / "context_review.json")

        tokens = read_page(book, 1)["tokens"]
        assert tokens[1]["txt"] == "الحد"          # the LLM suggestion, accepted
        assert tokens[3]["txt"] == "يعذ"           # the image fix, undone
        ev = tokens[3]["evidence"][-1]
        assert (ev["decided_by"], ev["rule"]) == ("human", "human-revert")

    @pytest.mark.parametrize("body, why", [
        ({"p009w000": "accept"}, "ids not on this page"),
        ({"p001w001": 3}, "a decision is"),
        ({"p001w001": "  "}, "a decision is"),
        (["accept"], "expected an object"),
    ])
    def test_refuses_bad_decisions(self, server, book, body, why):
        srv, _ = server
        status, out = call(srv, "POST", "/book-lighton/api/decisions", json.dumps(body))
        assert status == 400 and why in json.loads(out)["error"]
        assert json.loads((book / "context_decisions.json").read_text()) == {"p001w001": "reject"}

    def test_refuses_another_origin_and_another_host(self, server):
        srv, _ = server
        body = json.dumps({"p001w001": "accept"})
        assert call(srv, "POST", "/book-lighton/api/decisions", body,
                    {"Origin": "https://evil.example"})[0] == 403
        assert call(srv, "POST", "/book-lighton/api/decisions", body,
                    {"Host": "evil.example"})[0] == 403
        assert call(srv, "POST", "/book-lighton/api/decisions", body,
                    {"Content-Type": "text/plain"})[0] == 415

    def test_keeps_decisions_for_ids_not_on_the_page(self, server, book):
        (book / "context_decisions.json").write_text(
            json.dumps({"p001w001": "reject", "p007w002": "accept"}), encoding="utf-8")
        srv, _ = server
        call(srv, "POST", "/book-lighton/api/decisions", json.dumps({"p001w001": "الجدّ"}))
        assert json.loads((book / "context_decisions.json").read_text(encoding="utf-8")) == {
            "p001w001": "الجدّ", "p007w002": "accept"}


# ── overruling an automatic fix ──────────────────────────────────────────────

class TestOverruleFix:
    def run_with(self, book, decisions):
        (book / "context_decisions.json").write_text(json.dumps(decisions, ensure_ascii=False),
                                                     encoding="utf-8")
        apply_context_suggestions.run(book / "context_review.json")
        return read_page(book, 1)

    def test_no_decision_keeps_the_fix(self, book):
        rec = self.run_with(book, {})
        assert rec["tokens"][3]["txt"] == "يُعِدُّ" and rec["status"] == "spoken"

    def test_a_custom_word_replaces_the_fix(self, book):
        rec = self.run_with(book, {"p001w003": "يُعَدُّ"})
        tok = rec["tokens"][3]
        assert tok["txt"] == "يُعَدُّ" and tok["ocr_txt"] == "يعذ"
        assert tok["evidence"][-1]["rule"] == "human-custom"
        assert "human-review" in tok["fixes"] and rec["status"] == "extracted"

    def test_a_token_changed_since_the_fix_is_skipped(self, book, capsys):
        rec = read_page(book, 1)
        rec["tokens"][3]["txt"] = "شيء"
        (book / "p001.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

        rec = self.run_with(book, {"p001w003": "reject"})

        assert rec["tokens"][3]["txt"] == "شيء"
        assert "changed since the fix" in capsys.readouterr().out
