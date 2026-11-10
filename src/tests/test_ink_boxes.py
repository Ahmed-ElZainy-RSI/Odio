"""Word boxes fit the ink on the page. A synthetic page: 1000x1000, so 1% = 10px."""
import numpy as np
from PIL import Image

from pipeline import ink_boxes


def page(*rects, background=255):
    gray = np.full((1000, 1000), background, dtype=np.uint8)
    for x0, y0, x1, y1 in rects:
        # striped, because real letters don't fill their box; solid blocks read as illustration
        gray[y0:y1:3, x0:x1] = 0
        gray[y1 - 1, x0:x1] = 0
    return gray


def token(id_, txt="xxxx", box=(30, 10, 60, 3), raw=None):
    return {"id": id_, "txt": txt, "raw": raw or txt, "box": list(box)}


def edges(box):
    """Percent box back to pixels, minus the padding."""
    x, y, w, h = [v * 10 for v in box]
    pad = ink_boxes.PAD_PX
    return round(x) + pad, round(y) + pad, round(x + w) - pad, round(y + h) - pad


def test_a_word_shrinks_to_its_ink():
    boxes = ink_boxes.fit_boxes([token("a", box=(68, 10, 13, 3))], page((700, 105, 790, 125)))

    assert edges(boxes["a"]) == (700, 105, 790, 125)


def test_a_mark_above_the_line_is_part_of_the_word():
    gray = page((700, 105, 790, 125), (740, 92, 750, 105))

    assert edges(ink_boxes.fit_boxes([token("a", box=(68, 10, 13, 3))], gray)["a"])[1] == 92


def test_the_widest_gap_separates_equal_words():
    # one narrow gap inside each word, and a wide one between them
    gray = page((800, 105, 900, 125), (700, 105, 795, 125), (500, 105, 595, 125), (400, 105, 495, 125))

    boxes = ink_boxes.fit_boxes([token("right"), token("left")], gray)

    assert edges(boxes["right"])[0] == 700
    assert edges(boxes["left"])[2] == 595


def test_letter_count_beats_a_wide_gap_inside_a_long_word():
    # the wide gap at 780-800 would leave a one-letter stump; the narrower 380-390 fits the words
    gray = page((800, 105, 900, 125), (390, 105, 780, 125), (300, 105, 380, 125))
    tokens = [token("right", "x" * 10), token("left", "x")]

    boxes = ink_boxes.fit_boxes(tokens, gray)

    assert edges(boxes["right"])[0] == 390
    assert edges(boxes["left"])[2] == 380


def test_too_few_gaps_keeps_the_boxes():
    assert ink_boxes.fit_boxes([token("right"), token("left")], page((500, 105, 800, 125))) == {}


def test_punctuation_is_left_out():
    # opening guillemet on the right, then the word, then the closing one and a full stop
    gray = page((795, 105, 810, 125), (600, 105, 780, 125), (575, 105, 590, 125), (560, 105, 566, 125))

    boxes = ink_boxes.fit_boxes([token("a", "xxxxx", raw="«xxxxx».", box=(50, 10, 40, 3))], gray)

    assert edges(boxes["a"])[0] == 600
    assert edges(boxes["a"])[2] == 780


def test_a_word_with_no_punctuation_keeps_a_narrow_last_letter():
    gray = page((795, 105, 802, 125), (600, 105, 785, 125))

    assert edges(ink_boxes.fit_boxes([token("a", box=(50, 10, 40, 3))], gray)["a"])[2] == 802


def test_white_text_on_a_dark_band_is_found():
    gray = np.full((1000, 1000), 20, dtype=np.uint8)
    gray[105:125:3, 700:790] = 240
    gray[124, 700:790] = 240

    assert edges(ink_boxes.fit_boxes([token("a", box=(68, 10, 13, 3))], gray)["a"])[0] == 700


def test_a_line_over_a_busy_illustration_keeps_its_box():
    gray = np.random.default_rng(1).choice([0, 255], size=(1000, 1000)).astype(np.uint8)

    assert ink_boxes.fit_boxes([token("a", box=(68, 10, 13, 3))], gray) == {}


def test_a_line_placed_off_the_page_keeps_its_box():
    gray = page((700, 105, 790, 125))
    tokens = [token("off", box=(120, 10, 5, 3)), token("below", box=(30, 130, 10, 3))]

    assert ink_boxes.fit_boxes(tokens, gray) == {}


def test_lines_are_fitted_separately():
    gray = page((700, 105, 790, 125), (700, 205, 790, 225))
    tokens = [token("a", box=(68, 10, 13, 3)), token("b", box=(68, 20, 13, 3))]

    boxes = ink_boxes.fit_boxes(tokens, gray)

    assert edges(boxes["a"])[1] == 105
    assert edges(boxes["b"])[1] == 205


def test_an_unreadable_page_image_keeps_the_boxes(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(b"not an image")
    rec = {"tokens": [token("a", box=(68, 10, 13, 3))], "box_level": "line"}

    assert ink_boxes.fit_page(rec, path) == {"fitted": 0, "total": 1}
    assert rec["tokens"][0]["box"] == [68, 10, 13, 3]
    assert rec["box_level"] == "line"


def test_only_the_box_changes(tmp_path):
    path = tmp_path / "p.png"
    Image.fromarray(page((700, 105, 790, 125))).save(path)
    rec = {"tokens": [{**token("a", box=(68, 10, 13, 3)), "t": [0, 100]}], "box_level": "line"}

    report = ink_boxes.fit_page(rec, path)

    assert report == {"fitted": 1, "total": 1}
    assert rec["box_level"] == "word"
    assert {k: v for k, v in rec["tokens"][0].items() if k != "box"} == {
        "id": "a", "txt": "xxxx", "raw": "xxxx", "t": [0, 100]}
