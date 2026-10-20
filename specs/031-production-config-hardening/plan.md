# Plan: production config hardening

**Spec:** [spec.md](spec.md)

```
service/ai_engine/config/settings.py     ENVIRONMENT + model_validator
service/ai_engine/api/routers/ops.py     /ready includes config checks
service/tests/unit/test_config.py        unsafe combos
service/.env.example                     production section
service/docs/03-configuration.md, 08-operations.md
```
