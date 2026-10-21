"""Spec 013: a book's work directory is found by the PDF's content, not its filename."""
import json
from pathlib import Path

import pytest
from conftest import make_token, write_page

from pipeline import books


def pdf(dir_: Path, name: str, content: bytes = b"%PDF-1.7 book one") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / name
    path.write_bytes(content)
    return path


def index(root: Path) -> dict:
    return json.loads((root / "index.json").read_text(encoding="utf-8"))


def test_a_new_pdf_gets_a_readable_directory_and_an_index_entry(tmp_path):
    root = tmp_path / "work"
    source = pdf(tmp_path / "in", "النجوم.pdf")

    work = books.resolve_work_dir(source, root=root)

    assert work == root / "النجوم"
    bid = books.book_id(source)
    assert len(bid) == 16
    assert index(root)["books"][bid]["dirs"] == {"": "النجوم"}
    assert json.loads((work / "book.json").read_text(encoding="utf-8"))["id"] == bid


def test_a_renamed_pdf_resumes_the_same_directory(tmp_path, capsys):
    root = tmp_path / "work"
    first = books.resolve_work_dir(pdf(tmp_path / "in", "النجوم.pdf"), root=root)
    renamed = books.resolve_work_dir(pdf(tmp_path / "in", "النجوم - Copy.pdf"), root=root)
    assert renamed == first
    assert "same content" in capsys.readouterr().out
    assert len(index(root)["books"]) == 1


def test_variants_share_one_book_entry(tmp_path):
    root = tmp_path / "work"
    source = pdf(tmp_path / "in", "book.pdf")
    main = books.resolve_work_dir(source, root=root)
    lighton = books.resolve_work_dir(source, root=root, variant="lighton")
    assert (main.name, lighton.name) == ("book", "book-lighton")
    assert index(root)["books"][books.book_id(source)]["dirs"] == {"": "book", "lighton": "book-lighton"}


def test_a_different_pdf_with_the_same_name_never_writes_into_the_other_books_work(tmp_path, capsys):
    root = tmp_path / "work"
    first = pdf(tmp_path / "a", "book.pdf", b"%PDF first")
    other = pdf(tmp_path / "b", "book.pdf", b"%PDF second, different book")
    work_a = books.resolve_work_dir(first, root=root)
    write_page(work_a, 1, [make_token("p001w000", "كلمة")], pdf_sha256_16=books.book_id(first))

    work_b = books.resolve_work_dir(other, root=root)

    assert work_b != work_a
    assert work_b.name == f"book-{books.book_id(other)[:8]}"
    assert "belongs to a different PDF" in capsys.readouterr().out


def test_an_existing_directory_from_the_same_pdf_is_adopted(tmp_path):
    root = tmp_path / "work"
    source = pdf(tmp_path / "in", "book.pdf")
    legacy = root / "book"
    write_page(legacy, 1, [make_token("p001w000", "كلمة")], pdf_sha256_16=books.book_id(source))
    assert books.resolve_work_dir(source, root=root) == legacy


def test_a_deleted_directory_is_recreated(tmp_path):
    root = tmp_path / "work"
    source = pdf(tmp_path / "in", "book.pdf")
    work = books.resolve_work_dir(source, root=root)
    (work / "book.json").unlink()
    work.rmdir()
    assert books.resolve_work_dir(source, root=root) == work
    assert work.is_dir()


def test_migrate_indexes_existing_books_from_their_records_idempotently(tmp_path):
    root = tmp_path / "work"
    write_page(root / "النجوم", 1, [make_token("p001w000", "x")], pdf_sha256_16="aaaaaaaaaaaaaaaa")
    write_page(root / "النجوم-lighton", 1, [make_token("p001w000", "x")], pdf_sha256_16="aaaaaaaaaaaaaaaa")
    write_page(root / "other", 1, [make_token("p001w000", "x")], pdf_sha256_16="bbbbbbbbbbbbbbbb")
    (root / "no-records").mkdir()

    books.migrate(root)
    first = index(root)
    books.migrate(root)

    assert index(root) == first
    assert first["books"]["aaaaaaaaaaaaaaaa"]["dirs"] == {"": "النجوم", "lighton": "النجوم-lighton"}
    assert first["books"]["bbbbbbbbbbbbbbbb"]["dirs"] == {"": "other"}


def test_migrate_skips_a_directory_mixing_two_pdfs(tmp_path, capsys):
    root = tmp_path / "work"
    write_page(root / "mixed", 1, [make_token("p001w000", "x")], pdf_sha256_16="aaaaaaaaaaaaaaaa")
    write_page(root / "mixed", 2, [make_token("p002w000", "x")], pdf_sha256_16="bbbbbbbbbbbbbbbb")
    books.migrate(root)
    assert index(root)["books"] == {}
    assert "2 different PDFs" in capsys.readouterr().out


def test_run_full_pipeline_passes_each_pass_its_own_directory(tmp_path, monkeypatch):
    """No more moving the Gemini result aside so LightOn can borrow its path."""
    pytest.importorskip("google.genai")
    import run_full_pipeline as rfp

    monkeypatch.chdir(tmp_path)
    source = pdf(tmp_path / "in", "book.pdf")
    calls = []

    def fake_run_pipeline(pdf_path, ocr, tts, pages, work_dir):
        calls.append((ocr, work_dir))
        assert work_dir.is_dir()

    review = tmp_path / "work" / "book-lighton" / "ensemble_review.json"
    monkeypatch.setattr(rfp.main_pipeline, "run_pipeline", fake_run_pipeline)
    def fake_ensemble(gemini_dir, lighton_dir):
        review.write_text(json.dumps({"pages": {}}), encoding="utf-8")
        return review

    monkeypatch.setattr(rfp.ensemble_review, "run", fake_ensemble)
    for module, name in [(rfp.vlm_arbiter, "run"), (rfp.apply_corrections, "run"),
                         (rfp.run_tashkel, "run"), (rfp.run_speak_gemini, "run"),
                         (rfp.build_reader, "build"), (rfp.context_detector, "run"),
                         (rfp.build_context_review, "run")]:
        monkeypatch.setattr(module, name, lambda *a, **k: tmp_path / "x")

    rfp.run(source)

    assert calls == [("gemini", Path("work/book")), ("lighton", Path("work/book-lighton"))]
    assert sorted(p.name for p in (tmp_path / "work").iterdir()) == ["book", "book-lighton", "index.json"]
