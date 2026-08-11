"""Layer 04b — diacritise pages that don't already carry tashkeel.

Only pages already routed 'diacritise' by the ratio threshold in
run_extract.py / run_ocr_gemini.py / run_ocr_lighton.py reach this module. Diacritizing text that already
carries marks would strip a typesetter's tashkeel and rewrite it wrong — the
research note this engine choice comes from (models/ibn_battuta/Tashkeel
libraries and APIs - research.html) calls that out explicitly. The routing
decision enforces it, not this module.

Engine: arabic-diacritizer (BiLSTM + attention, sentence cache, MIT, CPU-only,
6.6% DER on Tashkeela). Chosen over a general LLM: the same research note
measured GPT-5.3 at 20.9% DER on the identical benchmark — three times worse,
and slower and more expensive to call per page. CATT scores better (~3.4%
DER) but needs a PyTorch/ONNX serving setup for a gain that doesn't matter at
this corpus size; arabic-diacritizer runs on CPU with no extra infrastructure.

The model diacritises a full sentence for context (case endings depend on
neighbouring words) but returns exactly one output word per input word. Tokens
are therefore matched back to their diacritised form by position, not by
re-tokenising the output — re-tokenising could silently drift a token off the
stable id everything downstream (boxes, timings) anchors to.
"""
from diacritize import Diacritizer

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = Diacritizer.from_pretrained()
    return _model


def diacritize_tokens(tokens):
    """Diacritise tokens[].txt in place, matched back by position.

    Returns (tokens, report). If the model ever returns a different word
    count than it was given, the page is left untouched rather than risk
    attaching a diacritised word to the wrong token id — that mismatch is
    reported so it can be checked by hand instead of silently corrupting the
    id-to-text mapping the rest of the pipeline depends on.
    """
    words = [t["txt"] for t in tokens]
    if not words:
        return tokens, {"diacritized": 0, "mismatch": False}

    out = _get_model().diacritize(" ".join(words)).split()

    if len(out) != len(words):
        return tokens, {"diacritized": 0, "mismatch": True,
                         "expected": len(words), "got": len(out)}

    for t, w in zip(tokens, out):
        t["txt"] = w
    return tokens, {"diacritized": len(tokens), "mismatch": False}
