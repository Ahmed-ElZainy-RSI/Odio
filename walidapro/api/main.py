from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import ai_jobs, diacritization, health, ocr, pdf, pipeline, review, tts

app = FastAPI(
    title="Arabic OCR / Diacritization / TTS API",
    description=(
        "REST API wrapping the Arabic book pipeline: OCR verification, "
        "final context review, visual diacritization, dual-voice TTS, "
        "and the orchestrated full-book job."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(pdf.router)
app.include_router(ocr.router)
app.include_router(review.router)
app.include_router(diacritization.router)
app.include_router(tts.router)
app.include_router(pipeline.router)
app.include_router(ai_jobs.router)
