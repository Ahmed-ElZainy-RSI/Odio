"""Property-based tests.

The example-based suites pin down the cases we thought of. These explore the input space we
did not: arbitrary Arabic and Latin text, arbitrary whitespace, arbitrary page numbers,
arbitrary timing sequences, arbitrary ledger shapes.

Each test states an invariant that must hold for *every* input, which is a stronger claim
than any number of examples. Where an invariant is load-bearing for the V1 contract -- the
Job A -> Job B id round trip, timing monotonicity, reason-string safety -- that is said in
the docstring, because a failing property here is a protocol bug, not a style issue.
"""

from __future__ import annotations

import json
from uuid import UUID

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_engine.artifacts.text_builder import build_text
from ai_engine.artifacts.timing_builder import build_timing_document
from ai_engine.artifacts.tokenizer import detokenize, tokenize
from ai_engine.domain.enums import JobType, PageState, TimingSource
from ai_engine.domain.errors import (
    MAX_REASON_LENGTH,
    ErrorCode,
    FailureReason,
    redact,
)
from ai_engine.domain.invariants import audit_pages
from ai_engine.domain.job import PageTask, canonical_payload_hash
from ai_engine.domain.tokens import (
    AudioFormat,
    BindReport,
    PageProvenance,
    TimedToken,
    is_monotonic,
    token_id,
)

FAST = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# Arabic letters, diacritics, Latin, digits and punctuation -- the real corpus mixes all of
# these, and the tokenizer must not care.
WORD_CHARS = st.characters(
    whitelist_categories=("Lu", "Ll", "Lo", "Mn", "Nd", "Po"),
    # Every character `str.split()` treats as whitespace, written as escapes so the
    # exotic ones (NBSP, LINE/PARAGRAPH SEPARATOR) are unambiguous in source.
    blacklist_characters=" \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u2028\u2029",
)
words = st.text(alphabet=WORD_CHARS, min_size=1, max_size=12).filter(
    lambda w: w.strip() == w and w != ""
)
word_lists = st.lists(words, min_size=0, max_size=25)
pages = st.integers(min_value=1, max_value=999)
whitespace = st.sampled_from([" ", "  ", "\t", "\n", "\n\n", " \t ", "   \n  "])


# ── Tokenizer ───────────────────────────────────────────────────────────────────


@FAST
@given(word_lists, pages)
def test_tokenize_always_produces_contiguous_page_prefixed_ids(
    word_list: list[str], page: int
) -> None:
    tokens = tokenize(" ".join(word_list), page)
    assert [t.id for t in tokens] == [token_id(page, i) for i in range(len(tokens))]


@FAST
@given(word_lists, pages)
def test_tokenize_detokenize_round_trips(word_list: list[str], page: int) -> None:
    text = " ".join(word_list)
    assert detokenize(tokenize(text, page)) == text


@FAST
@given(word_lists, whitespace, pages)
def test_tokenizing_is_insensitive_to_whitespace_shape(
    word_list: list[str], separator: str, page: int
) -> None:
    """Newlines and runs of spaces must not create empty tokens or shift any id."""
    padded = separator.join(word_list)
    assert [t.text for t in tokenize(padded, page)] == word_list


@FAST
@given(word_lists, pages)
def test_tokenize_is_deterministic(word_list: list[str], page: int) -> None:
    text = " ".join(word_list)
    assert tokenize(text, page) == tokenize(text, page)


@FAST
@given(st.text(max_size=60), pages)
def test_tokenize_never_produces_a_token_containing_whitespace(text: str, page: int) -> None:
    """This is what makes the Text artifact losslessly invertible."""
    for token in tokenize(text, page):
        assert token.text.strip() == token.text
        assert not any(c.isspace() for c in token.text)


# ── The Text artifact, and the Job A -> Job B hinge ─────────────────────────────


@FAST
@given(word_lists, pages)
def test_text_artifact_round_trips_back_to_identical_tokens(
    word_list: list[str], page: int
) -> None:
    """The whole Job A -> Job B contract.

    Job B receives only these bytes, so if this property fails the two jobs disagree about
    token ids and the reader cannot line up timings with text.
    """
    original = tokenize(" ".join(word_list), page)
    payload = build_text(original)
    assert tokenize(payload.decode("utf-8"), page) == original


@FAST
@given(word_lists)
def test_text_artifact_always_ends_with_exactly_one_newline(
    word_list: list[str],
) -> None:
    payload = build_text(tokenize(" ".join(word_list), 1))
    assert payload.endswith(b"\n")
    assert not payload.endswith(b"\n\n")


@FAST
@given(word_lists)
def test_text_artifact_is_valid_utf8_without_a_bom(word_list: list[str]) -> None:
    payload = build_text(tokenize(" ".join(word_list), 1))
    assert not payload.startswith(b"\xef\xbb\xbf")
    payload.decode("utf-8")  # must not raise


@FAST
@given(word_lists, pages)
def test_text_artifact_is_byte_stable(word_list: list[str], page: int) -> None:
    tokens = tokenize(" ".join(word_list), page)
    assert build_text(tokens) == build_text(tokens)


# ── Timing artifact ─────────────────────────────────────────────────────────────


@st.composite
def timed_token_sequences(draw: st.DrawFn) -> tuple[list[TimedToken], int]:
    """A monotonic, non-overlapping sequence -- exactly what `bind` guarantees."""
    page = draw(pages)
    count = draw(st.integers(min_value=0, max_value=20))
    gaps = draw(st.lists(st.integers(min_value=0, max_value=500), min_size=count, max_size=count))
    spans = draw(st.lists(st.integers(min_value=1, max_value=900), min_size=count, max_size=count))
    untimed_flags = draw(st.lists(st.booleans(), min_size=count, max_size=count))
    tokens: list[TimedToken] = []
    cursor = 0
    for index in range(count):
        identifier = token_id(page, index)
        if untimed_flags[index]:
            tokens.append(TimedToken.untimed(identifier, f"w{index}"))
            continue
        start = cursor + gaps[index]
        end = start + spans[index]
        cursor = end
        tokens.append(
            TimedToken(
                id=identifier,
                text=f"w{index}",
                start_ms=start,
                end_ms=end,
                source=draw(st.sampled_from(list(TimingSource))),
            )
        )
    return tokens, cursor + draw(st.integers(min_value=0, max_value=1000))


@FAST
@given(timed_token_sequences())
def test_monotonic_input_always_yields_a_valid_timing_document(
    payload: tuple[list[TimedToken], int],
) -> None:
    tokens, duration = payload
    document = build_timing_document(
        page_number=1,
        tokens=tokens,
        duration_ms=duration,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=PageProvenance(),
    )
    # Round-trips through JSON, which is how it actually travels.
    reparsed = json.loads(json.dumps(document, ensure_ascii=False))
    assert reparsed["duration_ms"] == duration
    assert len(reparsed["tokens"]) == len(tokens)


@FAST
@given(timed_token_sequences())
def test_timing_document_invariants_hold_for_every_consumer(
    payload: tuple[list[TimedToken], int],
) -> None:
    """The four guarantees a reader is allowed to rely on."""
    tokens, duration = payload
    document = build_timing_document(
        page_number=3,
        tokens=tokens,
        duration_ms=duration,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=PageProvenance(),
    )

    previous_end = 0
    for entry in document["tokens"]:
        # 1. `t_src` is present exactly when `t` is. There is no third state.
        assert ("t" in entry) == ("t_src" in entry)
        if "t" not in entry:
            continue
        start, end = entry["t"]
        # 2. monotonic and non-overlapping
        assert start >= previous_end
        # 3. well-ordered
        assert start <= end
        # 4. within the audio
        assert end <= duration
        previous_end = end


@FAST
@given(timed_token_sequences())
def test_anchor_ratio_is_always_a_probability(
    payload: tuple[list[TimedToken], int],
) -> None:
    tokens, _duration = payload
    report = BindReport.from_tokens(tokens)
    assert 0.0 <= report.anchor_ratio <= 1.0


@FAST
@given(timed_token_sequences())
def test_bind_report_counts_always_add_up(
    payload: tuple[list[TimedToken], int],
) -> None:
    tokens, _duration = payload
    report = BindReport.from_tokens(tokens)
    assert report.anchored + report.interpolated + report.untimed == report.tokens
    assert report.monotonic is is_monotonic(tokens)


# ── Error reasons ───────────────────────────────────────────────────────────────


@FAST
@given(st.sampled_from(list(ErrorCode)), st.text(max_size=2000))
def test_a_reason_is_always_bounded_and_code_prefixed(code: ErrorCode, detail: str) -> None:
    """The Backend stores and displays these, so an unbounded reason is their problem."""
    wire = FailureReason(code, detail).to_wire()
    assert len(wire) <= MAX_REASON_LENGTH
    assert wire.startswith(code.value)


@FAST
@given(
    st.sampled_from(list(ErrorCode)),
    st.text(max_size=200),
    st.sampled_from(
        [
            "https://s3.example.com/x?X-Amz-Signature=abc123",
            "Bearer eyJhbGciOiJSUzI1NiJ9.body.sig",
            "api_key=sk-live-secret",
            "/Users/someone/.env",
            "client_secret: hunter2",
        ]
    ),
)
def test_a_reason_never_carries_a_credential_however_it_is_embedded(
    code: ErrorCode, noise: str, secret: str
) -> None:
    wire = FailureReason(code, f"{noise} {secret} {noise}").to_wire()
    for marker in ("X-Amz-Signature", "eyJ", "sk-live", "hunter2", "/Users/"):
        assert marker not in wire


@FAST
@given(st.text(max_size=300))
def test_redaction_is_idempotent(text: str) -> None:
    """Redacting twice must not corrupt an already-safe string."""
    once = redact(text)
    assert redact(once) == once


# ── Payload hashing ─────────────────────────────────────────────────────────────


identifiers = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_/"),
    min_size=1,
    max_size=20,
)


@FAST
@given(identifiers, identifiers, identifiers, st.one_of(st.none(), pages))
def test_payload_hash_is_deterministic(
    file_id: str, folder_id: str, path_prefix: str, page: int | None
) -> None:
    job_type = (
        JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION
        if page is not None
        else JobType.PAGES_IMG_AND_TEXT_EXTRACTION
    )
    args = {
        "job_type": job_type,
        "file_id": file_id,
        "folder_id": folder_id,
        "path_prefix": path_prefix,
        "page_number": page,
    }
    assert canonical_payload_hash(**args) == canonical_payload_hash(**args)


@FAST
@given(identifiers, identifiers)
def test_a_different_file_id_always_changes_the_hash(first: str, second: str) -> None:
    """Otherwise a genuinely different job would be silently accepted as a duplicate."""
    if first == second:
        return
    base = {
        "job_type": JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        "folder_id": "d",
        "path_prefix": "p",
        "page_number": None,
    }
    assert canonical_payload_hash(file_id=first, **base) != canonical_payload_hash(
        file_id=second, **base
    )


# ── INVARIANT-1 audit ───────────────────────────────────────────────────────────

JOB_UUID = UUID(int=42)
page_states = st.sampled_from(list(PageState))


@FAST
@given(
    st.lists(pages, min_size=0, max_size=12, unique=True),
    st.data(),
)
def test_completion_is_exactly_all_expected_pages_terminal(
    expected: list[int], data: st.DataObject
) -> None:
    """The definition of INVARIANT-1, checked over arbitrary ledgers."""
    states = data.draw(st.lists(page_states, min_size=len(expected), max_size=len(expected)))
    ledger = [
        PageTask(job_id=JOB_UUID, page_number=number, state=state)
        for number, state in zip(expected, states, strict=True)
    ]
    audit = audit_pages(expected, ledger)
    all_terminal = all(state.is_terminal for state in states)
    assert audit.is_complete is all_terminal
    assert audit.is_well_formed  # exactly the expected set, once each


@FAST
@given(st.lists(pages, min_size=1, max_size=10, unique=True))
def test_a_missing_page_always_blocks_completion(expected: list[int]) -> None:
    """The spec's 29-of-30 case, generalised."""
    ledger = [
        PageTask(job_id=JOB_UUID, page_number=number, state=PageState.REPORTED_COMPLETED)
        for number in expected[:-1]
    ]
    audit = audit_pages(expected, ledger)
    assert not audit.is_complete
    assert audit.missing == (expected[-1],)


@FAST
@given(st.lists(pages, min_size=1, max_size=8, unique=True))
def test_a_duplicated_ledger_row_is_never_well_formed(expected: list[int]) -> None:
    ledger = [
        PageTask(job_id=JOB_UUID, page_number=number, state=PageState.REPORTED_COMPLETED)
        for number in expected
    ] + [PageTask(job_id=JOB_UUID, page_number=expected[0], state=PageState.REPORTED_FAILED)]
    audit = audit_pages(expected, ledger)
    assert not audit.is_well_formed
    assert expected[0] in audit.duplicated


@FAST
@given(st.lists(pages, min_size=1, max_size=8, unique=True), pages)
def test_an_unexpected_page_is_never_well_formed(expected: list[int], stray: int) -> None:
    if stray in expected:
        return
    ledger = [
        PageTask(job_id=JOB_UUID, page_number=number, state=PageState.REPORTED_COMPLETED)
        for number in [*expected, stray]
    ]
    audit = audit_pages(expected, ledger)
    assert not audit.is_well_formed
    assert stray in audit.unexpected


@FAST
@given(pages)
def test_a_single_page_job_audits_correctly_for_any_page_number(page: int) -> None:
    """Job B may be asked for page 7 of a book. A count-based audit would call that page
    unexpected and pages 1-6 missing."""
    ledger = [PageTask(job_id=JOB_UUID, page_number=page, state=PageState.REPORTED_COMPLETED)]
    audit = audit_pages([page], ledger)
    assert audit.is_complete
    assert audit.expected == 1
