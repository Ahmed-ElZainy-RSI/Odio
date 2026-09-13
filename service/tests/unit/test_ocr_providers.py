"""Paid OCR adapters, with the providers faked.

Zero paid calls: the Gemini client is a stub object and LightOn is served by an
`httpx.MockTransport`. What is *not* faked is the legacy tokenisation -- `lines_to_tokens`,
`text_to_tokens`, `clean_markdown` and `clean_word` all run for real, because that is where
the Arabic domain knowledge lives and where a wrapping mistake would show up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from ai_engine.adapters.ai.ocr_providers import (
    GeminiVisionOcr,
    LightOnOcr,
    build_ocr_engine,
)
from ai_engine.adapters.ai.ocr_textlayer import TextLayerOcr
from ai_engine.adapters.ai.renderer_pymupdf import PyMuPdfRenderer
from ai_engine.domain.errors import (
    AiInferenceError,
    ModelUnavailableError,
    SourceInvalidError,
)
from ai_engine.observability.metrics import INFERENCE_CALLS, INFERENCE_FAILURES, Metrics

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_AVAILABLE = (REPO_ROOT / "src" / "pipeline" / "ocr_gemini.py").exists()

pytestmark = pytest.mark.skipif(not LEGACY_AVAILABLE, reason="the legacy pipeline is not present")

# Two real Arabic lines, with box_2d as the model emits it: [ymin, xmin, ymax, xmax]
# normalised to 0-1000.
GEMINI_LINES = [
    {"text": "طَارُ الْعُصْفُورُ صَوْصَوْ", "box_2d": [100, 200, 160, 800]},
    {"text": "وَرَاءَ فُلْفُل", "box_2d": [200, 300, 260, 700]},
]


class StubGeminiClient:
    """Mimics the `google.genai` client surface the adapter uses."""

    def __init__(
        self, *, parsed: Any = None, text: str | None = None, raises: Exception | None = None
    ) -> None:
        self.models = self
        self.calls: list[dict] = []
        self._parsed = parsed
        self._text = text
        self._raises = raises

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return type("Response", (), {"parsed": self._parsed, "text": self._text})()


def gemini(**kwargs: Any) -> GeminiVisionOcr:
    kwargs.setdefault("legacy_repo_root", REPO_ROOT)
    return GeminiVisionOcr(**kwargs)


# ── Gemini: the happy path ──────────────────────────────────────────────────────


def test_gemini_lines_become_word_tokens_with_line_boxes() -> None:
    metrics = Metrics()
    engine = gemini(client=StubGeminiClient(parsed=GEMINI_LINES), metrics=metrics)

    tokens, provenance = engine.extract_page(
        page_image=b"\xff\xd8\xff jpeg", pdf_path=None, page_number=3
    )

    assert [t.id for t in tokens] == [f"p003w{i:03d}" for i in range(5)]
    assert [t.text for t in tokens] == [
        "طَارُ",
        "الْعُصْفُورُ",
        "صَوْصَوْ",
        "وَرَاءَ",
        "فُلْفُل",
    ]
    # Per-line geometry, subdivided by letter count -- approximate but present.
    assert all(t.box is not None for t in tokens)
    assert provenance.box_level == "line"
    assert provenance.ocr_engine.startswith("gemini:")


def test_gemini_word_boxes_stay_inside_their_line() -> None:
    """The line box is the only real measurement, so word boxes must not exceed it."""
    engine = gemini(client=StubGeminiClient(parsed=GEMINI_LINES[:1]))
    tokens, _ = engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)
    # Line 1 spans x 20.0 -> 80.0 as a percentage of the page.
    for token in tokens:
        assert token.box is not None
        x, _y, width, _height = token.box
        assert 20.0 - 0.01 <= x
        assert x + width <= 80.0 + 0.01


def test_gemini_counts_its_paid_call() -> None:
    """The only cost visibility that exists."""
    metrics = Metrics()
    engine = gemini(client=StubGeminiClient(parsed=GEMINI_LINES), metrics=metrics)
    engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)

    assert (
        metrics.counter_value(
            INFERENCE_CALLS,
            {"provider": "gemini", "model": "gemini-3.6-flash", "stage": "ocr"},
        )
        == 1
    )


def test_gemini_sends_the_image_and_the_prompt() -> None:
    client = StubGeminiClient(parsed=GEMINI_LINES)
    gemini(client=client).extract_page(page_image=b"the-image-bytes", pdf_path=None, page_number=1)
    contents = client.calls[0]["contents"]
    assert len(contents) == 2
    assert "bounding box" in contents[1]


def test_gemini_uses_the_configured_model_not_a_constant() -> None:
    """Model ids are configuration, so a deprecation is not a code change."""
    client = StubGeminiClient(parsed=[])
    gemini(client=client, model_id="gemini-9-experimental").extract_page(
        page_image=b"jpeg", pdf_path=None, page_number=1
    )
    assert client.calls[0]["model"] == "gemini-9-experimental"


def test_gemini_falls_back_to_parsing_the_text_body() -> None:
    """Some SDK versions populate `text` rather than `parsed`."""
    import json

    engine = gemini(client=StubGeminiClient(parsed=None, text=json.dumps(GEMINI_LINES)))
    tokens, _ = engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=2)
    assert len(tokens) == 5


# ── Gemini: degradation and failure ─────────────────────────────────────────────


def test_an_illustration_only_page_yields_no_tokens_and_is_not_an_error() -> None:
    """The Backend still waits for that page, so it must be completable."""
    engine = gemini(client=StubGeminiClient(parsed=[]))
    tokens, provenance = engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=9)
    assert tokens == []
    assert provenance.box_level == "line"


def test_lines_that_clean_down_to_nothing_are_dropped() -> None:
    """Pure-punctuation 'words' would otherwise become invisible, untimed spans."""
    engine = gemini(
        client=StubGeminiClient(parsed=[{"text": "-- ... --", "box_2d": [0, 0, 10, 10]}])
    )
    tokens, _ = engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)
    assert tokens == []


def test_a_provider_error_becomes_a_typed_domain_error() -> None:
    metrics = Metrics()
    engine = gemini(
        client=StubGeminiClient(raises=RuntimeError("upstream exploded")), metrics=metrics
    )
    with pytest.raises(AiInferenceError, match="OCR provider failed"):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)

    assert (
        metrics.counter_value(
            INFERENCE_FAILURES,
            {
                "provider": "gemini",
                "model": "gemini-3.6-flash",
                "error_code": "AI_INFERENCE_ERROR",
            },
        )
        == 1
    )


def test_an_empty_response_is_an_error_not_an_empty_page() -> None:
    """Silence from the provider is a failure; an empty list is a blank page. The
    difference matters -- one should retry, the other should complete."""
    engine = gemini(client=StubGeminiClient(parsed=None, text=None))
    with pytest.raises(AiInferenceError, match="no content"):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)


def test_unparseable_json_is_an_error() -> None:
    engine = gemini(client=StubGeminiClient(parsed=None, text="not json at all"))
    with pytest.raises(AiInferenceError, match="unparseable"):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)


def test_an_unexpected_shape_is_an_error() -> None:
    engine = gemini(client=StubGeminiClient(parsed={"lines": []}))
    with pytest.raises(AiInferenceError, match="unexpected shape"):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)


def test_gemini_without_an_api_key_is_model_unavailable() -> None:
    """Distinct from an inference error: this is configuration, and retrying will not fix it."""
    engine = gemini(api_key=None, client=None)
    with pytest.raises(ModelUnavailableError, match="GEMINI_API_KEY"):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)


def test_gemini_needs_a_rendered_image() -> None:
    engine = gemini(client=StubGeminiClient(parsed=[]))
    with pytest.raises(SourceInvalidError, match="rendered page image"):
        engine.extract_page(page_image=None, pdf_path="/tmp/x.pdf", page_number=1)


# ── LightOn ─────────────────────────────────────────────────────────────────────


def small_pdf(pages: int = 4) -> bytes:
    import fitz

    doc = fitz.open()
    for index in range(pages):
        doc.new_page().insert_text((72, 144), f"page {index + 1}", fontsize=12)
    payload = doc.tobytes()
    doc.close()
    return payload


def lighton_transport(
    *, markdown: str = "طَارُ الْعُصْفُورُ", status: int = 200, body: Any = None
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status >= 400:
            return httpx.Response(status)
        payload = body if body is not None else {"result": {"pages": [{"markdown": markdown}]}}
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler), seen


async def run_lighton(tmp_path: Path, **kwargs: Any):
    transport, seen = lighton_transport(**kwargs)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(small_pdf())
    metrics = Metrics()
    async with httpx.AsyncClient(transport=transport) as client:
        engine = LightOnOcr(
            client=client,
            api_key="test-key",
            legacy_repo_root=REPO_ROOT,
            metrics=metrics,
        )
        result = await engine.extract_page_async(pdf_path=str(pdf), page_number=4)
    return result, seen, metrics


async def test_lighton_markdown_becomes_tokens_with_no_geometry(tmp_path: Path) -> None:
    """`box` is omitted rather than fabricated: a reader that trusted invented boxes
    would highlight the wrong words."""
    (tokens, provenance), _seen, _metrics = await run_lighton(tmp_path)

    assert [t.text for t in tokens] == ["طَارُ", "الْعُصْفُورُ"]
    assert [t.id for t in tokens] == ["p004w000", "p004w001"]
    assert all(t.box is None for t in tokens)
    assert provenance.box_level == "none"


async def test_lighton_strips_markdown_and_latex_noise(tmp_path: Path) -> None:
    """The legacy cleaner runs for real: headings, images, page numbers and stray LaTeX
    all leak out of this provider."""
    messy = "# عنوان\n\n![img](x.png)\n\nطَارُ الْعُصْفُورُ\n\n7\n"
    (tokens, _), _seen, _metrics = await run_lighton(tmp_path, markdown=messy)
    assert [t.text for t in tokens] == ["عنوان", "طَارُ", "الْعُصْفُورُ"]


async def test_lighton_sends_a_single_page_pdf(tmp_path: Path) -> None:
    """One request maps to exactly one page record."""
    _result, seen, _metrics = await run_lighton(tmp_path)
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert b"%PDF-" in seen[0].content


async def test_lighton_hits_the_hosted_endpoint_by_default() -> None:
    """The legacy default builds `http://localhost:8000/v1/api/v3/parse`, which is a
    nonsense URL and is set in no .env in the repo."""
    async with httpx.AsyncClient() as client:
        engine = LightOnOcr(client=client, api_key="k")
    assert engine.endpoint == "https://api.lighton.ai/api/v3/parse"


async def test_lighton_counts_its_paid_call(tmp_path: Path) -> None:
    _result, _seen, metrics = await run_lighton(tmp_path)
    assert (
        metrics.counter_value(
            INFERENCE_CALLS,
            {"provider": "lighton", "model": "lighton/parse-v3", "stage": "ocr"},
        )
        == 1
    )


async def test_lighton_blank_page_yields_no_tokens(tmp_path: Path) -> None:
    (tokens, _), _seen, _metrics = await run_lighton(tmp_path, body={"result": {"pages": []}})
    assert tokens == []


async def test_lighton_http_error_becomes_an_inference_error(tmp_path: Path) -> None:
    with pytest.raises(AiInferenceError):
        await run_lighton(tmp_path, status=500)


async def test_lighton_malformed_response_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(AiInferenceError, match=r"result\.pages"):
        await run_lighton(tmp_path, body={"unexpected": True})


async def test_lighton_without_an_api_key_is_model_unavailable(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(small_pdf())
    async with httpx.AsyncClient() as client:
        engine = LightOnOcr(client=client, api_key=None, legacy_repo_root=REPO_ROOT)
        with pytest.raises(ModelUnavailableError, match="LIGHTON_API_KEY"):
            await engine.extract_page_async(pdf_path=str(pdf), page_number=1)


async def test_lighton_rejects_an_out_of_range_page(tmp_path: Path) -> None:
    transport, _seen = lighton_transport()
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(small_pdf())
    async with httpx.AsyncClient(transport=transport) as client:
        engine = LightOnOcr(client=client, api_key="k", legacy_repo_root=REPO_ROOT)
        with pytest.raises(SourceInvalidError, match="outside the document"):
            await engine.extract_page_async(pdf_path=str(pdf), page_number=99)


# ── Engine selection ────────────────────────────────────────────────────────────


class FakeSettings:
    legacy_repo_root = REPO_ROOT
    ocr_gemini_model = "gemini-3.6-flash"
    lighton_base_url = "https://api.lighton.ai"

    class _Secret:
        def __init__(self, value: str) -> None:
            self._value = value

        def get_secret_value(self) -> str:
            return self._value

    def __init__(self, ocr_engine: str, api_key: str = "k") -> None:
        self.ocr_engine = ocr_engine
        self.gemini_api_key = self._Secret(api_key)
        self.lighton_api_key = self._Secret(api_key)


class FakeRenderer:
    def __init__(self, *, has_layer: bool) -> None:
        self._has_layer = has_layer

    def has_text_layer(self, pdf_path: str, *, min_words: int = 50) -> bool:
        return self._has_layer


async def test_auto_prefers_the_free_text_layer_when_one_exists() -> None:
    """25 of the 36 sample books qualify. The default should never spend money it does
    not have to."""
    async with httpx.AsyncClient() as client:
        engine = build_ocr_engine(
            settings=FakeSettings("auto"),
            renderer=FakeRenderer(has_layer=True),
            http_client=client,
            metrics=Metrics(),
            pdf_path="/tmp/book.pdf",
        )
    assert isinstance(engine, TextLayerOcr)


async def test_auto_falls_back_to_a_paid_engine_for_a_scanned_book() -> None:
    async with httpx.AsyncClient() as client:
        engine = build_ocr_engine(
            settings=FakeSettings("auto"),
            renderer=FakeRenderer(has_layer=False),
            http_client=client,
            metrics=Metrics(),
            pdf_path="/tmp/scan.pdf",
        )
    assert isinstance(engine, GeminiVisionOcr)


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("textlayer", TextLayerOcr), ("gemini", GeminiVisionOcr), ("lighton", LightOnOcr)],
)
async def test_an_explicit_choice_is_honoured(choice: str, expected: type) -> None:
    async with httpx.AsyncClient() as client:
        engine = build_ocr_engine(
            settings=FakeSettings(choice),
            renderer=FakeRenderer(has_layer=True),
            http_client=client,
            metrics=Metrics(),
            pdf_path="/tmp/book.pdf",
        )
    assert isinstance(engine, expected)


async def test_an_unknown_engine_is_refused_at_construction() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelUnavailableError, match="unknown ocr_engine"):
            build_ocr_engine(
                settings=FakeSettings("telepathy"),
                renderer=FakeRenderer(has_layer=False),
                http_client=client,
                metrics=Metrics(),
            )


@pytest.mark.parametrize(
    ("choice", "env_var"),
    [("gemini", "GEMINI_API_KEY"), ("lighton", "LIGHTON_API_KEY")],
)
async def test_a_paid_engine_without_its_key_is_refused_at_selection(
    choice: str, env_var: str
) -> None:
    """The key check belongs at selection, not at the first page.

    Constructing the engine happens once per document; calling it happens once per page.
    Refusing early turns N identical page failures into one legible job failure.
    """
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelUnavailableError, match=env_var):
            build_ocr_engine(
                settings=FakeSettings(choice, api_key=""),
                renderer=FakeRenderer(has_layer=False),
                http_client=client,
                metrics=Metrics(),
            )


async def test_auto_falls_back_to_gemini_when_there_is_no_text_layer() -> None:
    async with httpx.AsyncClient() as client:
        engine = build_ocr_engine(
            settings=FakeSettings("auto", api_key="k"),
            renderer=FakeRenderer(has_layer=False),
            http_client=client,
            metrics=Metrics(),
            pdf_path="/tmp/scan.pdf",
        )
    assert isinstance(engine, GeminiVisionOcr)


SAMPLE_PDF = REPO_ROOT / "assets" / "PDF Samples" / "النجوم.pdf"


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="sample corpus PDF is not available")
async def test_auto_resolves_to_the_free_text_layer_for_a_real_book_that_has_one() -> None:
    """The default must not spend money on a book whose text is already extractable.

    Checked against a real corpus PDF with the real renderer -- 25 of the 36 sample books
    qualify, and picking the paid path for them would be a pure waste.
    """
    from ai_engine.adapters.ai.ocr_textlayer import TextLayerOcr

    async with httpx.AsyncClient() as client:
        engine = build_ocr_engine(
            settings=FakeSettings("auto", api_key=""),
            renderer=PyMuPdfRenderer(),
            http_client=client,
            metrics=Metrics(),
            pdf_path=str(SAMPLE_PDF),
        )
    assert isinstance(engine, TextLayerOcr)
