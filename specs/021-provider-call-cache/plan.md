# Plan: provider call cache + atomic writes

**Spec:** [spec.md](spec.md)

```
service/ai_engine/ports/cache.py                       ProviderCache Protocol
service/ai_engine/domain/cache_key.py                  canonical key (stdlib)
service/ai_engine/adapters/persistence/cache_fs.py     filesystem adapter, atomic
service/ai_engine/adapters/persistence/cache_sql.py    table in the job store DB
src/pipeline/atomic.py                                 atomic_write_text/bytes (port of walidapro utils)
service/tests/unit/test_cache.py
```

Wrap usage in adapters (OCR, TTS, verifier sub-calls), not in the application layer.
Reference: walidapro cache table in `walidapro/docs/architecture.md` § Caching.
