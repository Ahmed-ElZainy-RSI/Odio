"""An empty answer from the TTS model is retried, and never crashes the book."""
from types import SimpleNamespace

import pytest

from pipeline import failures, tts_gemini


def audio_reply(pcm=b"\0\0" * 2400):
    part = SimpleNamespace(inline_data=SimpleNamespace(data=pcm))
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
                           usage_metadata=None)


def empty_reply():
    return SimpleNamespace(candidates=[SimpleNamespace(content=None)], usage_metadata=None)


def client(*replies):
    calls = iter(replies)
    return SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: next(calls)))


def test_an_empty_answer_is_tried_again():
    pcm, duration_ms = tts_gemini.synthesize(client(empty_reply(), audio_reply()), "نص")

    assert pcm == b"\0\0" * 2400
    assert duration_ms == 100


def test_only_so_many_attempts_and_the_page_fails_alone(tmp_path):
    replies = [empty_reply()] * tts_gemini.MAX_ATTEMPTS

    with pytest.raises(tts_gemini.NoAudioReturned) as caught:
        tts_gemini.synthesize(client(*replies), "نص")

    # "page", not "bug": a bug would abort every remaining page of the book
    assert failures.handle(tmp_path, 1, "tts", caught.value, "p001") == "page"


def sent_text(**kwargs):
    sent = []
    fake = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: sent.append(kw["contents"]) or audio_reply()))
    tts_gemini.synthesize(fake, "نص", **kwargs)
    return sent[0]


def test_no_style_sends_only_the_text():
    assert sent_text() == "نص"


def test_a_style_goes_before_the_text():
    assert sent_text(style="احك: ") == "احك: نص"
