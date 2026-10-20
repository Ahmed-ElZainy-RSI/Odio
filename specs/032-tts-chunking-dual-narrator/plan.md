# Plan: TTS chunking + dual narrator

**Spec:** [spec.md](spec.md)

```
service/ai_engine/adapters/ai/tts_chunker.py      ← walidapro/ocr_verifier/tts/text_chunker.py
service/ai_engine/adapters/ai/audio_merge.py      ← walidapro/ocr_verifier/tts/audio_manager.py
service/ai_engine/adapters/ai/tts.py              ChunkedTts wrapper around GeminiTts
service/ai_engine/application/run_audio_job.py    timing offset per chunk
src/pipeline/tts_gemini.py                        same chunker for the CLI
service/tests/unit/test_tts_chunker.py, test_audio_merge.py
```

Chunk boundaries must fall on token boundaries, so chunks are built from tokens (joined with
spaces), not from raw text. That keeps the id mapping trivial.
