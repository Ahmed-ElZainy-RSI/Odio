# Plan: front matter handling

**Spec:** [spec.md](spec.md)

1. Get the product decision (spec § Decision needed).
2. `src/pipeline/front_matter.py` with `is_front_matter()` (regexes for ISBN `97[89][-\d ]{10,}`, `ردمك`, `رقم الإيداع`, `حقوق الطبع`, digit ratio).
3. Replace the `rec["page"] == 1` check in `run_speak_hume.py:25`; add the same check to `run_speak_gemini.py:do_page`.
4. Service: Job B checks it before synthesis. A skipped page still gets a terminal callback (`completed` with no audio, or `failed` with reason `front_matter`, per the Backend's answer).

```
src/pipeline/front_matter.py, run_speak_hume.py, run_speak_gemini.py
service/ai_engine/application/run_audio_job.py
src/tests/test_front_matter.py
```
