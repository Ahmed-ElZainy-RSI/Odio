from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ocr_verifier.tts.pipeline import TTSPipeline

from ..schemas import ErrorResponse, TTSRequest, TTSResponse
from ..storage import TTS_DIR, new_id

router = APIRouter(prefix="/api/v1/tts", tags=["5. TTS"])


@router.post(
    "/generate",
    response_model=TTSResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse, "description": "Empty text or invalid narrator_type."},
        502: {"model": ErrorResponse, "description": "TTS generation failed or quality gate rejected the audio."},
        503: {"model": ErrorResponse, "description": "GEMINI_TTS_API_KEY not configured."},
    },
    summary="Generate narrated Arabic audio for arbitrary text",
)
def generate(payload: TTSRequest) -> TTSResponse:
    """
    **Input:** JSON `{"text": "...", "narrator_type": "female"|"male",
    "voice": null, "input_name": null}`. `narrator_type` defaults to
    `"female"` (Leda); pass `voice` to override with an explicit Gemini
    voice name.

    **Output:** generation metadata (`tts_id`, chunk counts, duration) and
    `audio_url` to download the WAV via `GET /api/v1/tts/{tts_id}/audio`.

    Audio duration is sanity-checked against word count; an abnormally
    short/long chunk (silence or repetition) is regenerated once, and if
    it still fails, the request fails rather than returning bad audio.
    """
    if payload.narrator_type not in {"female", "male"}:
        raise HTTPException(400, "narrator_type must be 'female' or 'male'.")

    tts_id = new_id()
    output_dir = TTS_DIR / tts_id

    try:
        pipeline = TTSPipeline(
            output_dir=output_dir,
            voice=payload.voice,
            narrator_type=payload.narrator_type,
        )
    except RuntimeError as exc:
        raise HTTPException(503, f"TTS engine not configured: {exc}") from exc

    try:
        result = pipeline.run(
            text=payload.text,
            input_name=payload.input_name or "api_request",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"TTS generation failed: {exc}") from exc

    if not result.final_audio_path:
        raise HTTPException(
            502,
            f"TTS generation failed for {result.failed_chunks} chunk(s).",
        )

    return TTSResponse(
        tts_id=tts_id,
        narrator_type=payload.narrator_type,
        voice=result.voice,
        model=result.model,
        total_chunks=result.total_chunks,
        successful_chunks=result.successful_chunks,
        failed_chunks=result.failed_chunks,
        total_characters=result.total_characters,
        total_duration_seconds=result.total_duration_seconds,
        audio_available=bool(result.final_audio_path),
        audio_url=f"/api/v1/tts/{tts_id}/audio",
    )


@router.get(
    "/{tts_id}/audio",
    status_code=200,
    responses={
        404: {"model": ErrorResponse, "description": "No audio for this tts_id."},
        200: {"content": {"audio/wav": {}}, "description": "WAV audio file."},
    },
    summary="Download previously generated TTS audio",
)
def get_audio(tts_id: str):
    """
    **Input:** `tts_id` returned by `POST /api/v1/tts/generate`.

    **Output:** raw `audio/wav` bytes.
    """
    audio_path = TTS_DIR / tts_id / "final_audio.wav"

    if not audio_path.exists():
        raise HTTPException(404, "Audio not found for this tts_id.")

    return FileResponse(
        audio_path,
        media_type="audio/wav",
        filename=f"{tts_id}.wav",
    )
