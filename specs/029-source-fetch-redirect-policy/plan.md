# Plan: redirect policy

**Spec:** [spec.md](spec.md)

```
service/ai_engine/adapters/storage/source_fetcher.py   follow_redirects=False on send(); 3xx branch
service/ai_engine/domain/errors.py                     SOURCE_REDIRECT_REFUSED (non-retryable)
service/tests/unit/test_source_fetcher.py              respx 302 cases (metadata IP, RFC1918, allowed host)
```
