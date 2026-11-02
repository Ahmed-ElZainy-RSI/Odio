"""Which pages the reader renders: audio is required, a page image is not."""
from pipeline import build_reader

from conftest import make_token, write_page


def spoken_page(book, page, *, image=False):
    tok = make_token(f"p{page:03d}w000", "أرنوب")
    tok.update({"t": [0, 500], "t_src": "anchor"})
    write_page(book, page, [tok], audio=f"audio/p{page:03d}.wav", duration_ms=500,
               box_level="none")
    (book / "audio").mkdir(exist_ok=True)
    (book / "audio" / f"p{page:03d}.wav").write_bytes(b"RIFF")
    if image:
        (book / "pages").mkdir(exist_ok=True)
        (book / "pages" / f"p{page:03d}.jpg").write_bytes(b"jpeg")


def test_a_lighton_only_book_gets_a_text_only_reader(tmp_path):
    # LightOn's OCR path saves no page images; the reader must still build
    book = tmp_path / "book"
    spoken_page(book, 1)
    spoken_page(book, 2)

    html = build_reader.build(book).read_text(encoding="utf-8")

    assert "text view only" in html
    assert "const HAS_COMPARE_LAYOUT=false" in html
    assert '"page":2' in html


def test_a_page_without_audio_is_still_skipped(tmp_path, capsys):
    book = tmp_path / "book"
    spoken_page(book, 1)
    write_page(book, 2, [make_token("p002w000", "أرنوب")], box_level="none")

    html = build_reader.build(book).read_text(encoding="utf-8")

    assert '"page":1' in html and '"page":2' not in html
    assert "p002" in capsys.readouterr().out


def test_a_page_with_an_image_but_no_boxes_shows_the_image(tmp_path):
    # a LightOn page: its own rendered image, no word geometry
    book = tmp_path / "book"
    spoken_page(book, 1, image=True)

    html = build_reader.build(book).read_text(encoding="utf-8")

    assert "const HAS_COMPARE_LAYOUT=true" in html
    assert '"compare":true,"boxed":false' in html
    assert "text view only" not in html
