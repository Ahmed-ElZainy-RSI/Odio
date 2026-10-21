"""Configuration.

Environment only, validated at startup, injected rather than imported as a global.

Three rules, each written against a specific defect in the existing code:

1. **No ``load_dotenv()`` anywhere in this package.** The legacy modules call it at import
   time in 11 places, which means a stray `.env` on a container's filesystem silently
   overrides orchestrator-injected secrets, with no log line and no error.
   :func:`assert_no_dotenv_on_disk` turns that silent override into a loud failure.
2. **Fail fast.** A missing required secret aborts startup naming the key, rather than
   surfacing as a 500 on the first job.
3. **Not a module-level singleton.** ``walidapro/ocr_verifier/config.py`` freezes every
   value at import, so a long-lived worker cannot pick up a rotated credential. Settings
   here are constructed once at startup and passed by dependency injection.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from ai_engine.domain.budget import ProviderLimit, parse_limits
from ai_engine.domain.enums import QualityTier

OCR_ENGINES = frozenset({"auto", "textlayer", "gemini", "lighton"})
DIACRITIZERS = frozenset({"none", "local"})
"""Selectable OCR engines. ``auto`` prefers the free text layer when the PDF has one."""


class Role(StrEnum):
    API = "api"
    WORKER = "worker"
    RECONCILER = "reconciler"


class Settings(BaseSettings):
    """The complete V1 configuration surface.

    ``env_file`` is deliberately NOT set: see rule 1 above.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        # No env_file. Configuration comes from the process environment only.
    )

    # ── Role and server ──────────────────────────────────────────────────────────
    ai_engine_role: Role = Role.API
    ai_engine_port: int = 9099
    ai_engine_log_level: str = "INFO"
    ai_engine_version: str = "0.1.0"
    ai_engine_json_logs: bool = True

    # ── Backend integration (REQ-CFG-01) ─────────────────────────────────────────
    backend_base_url: str = "https://localhost:6060"
    token_url: str = "http://localhost:9090/realms/reading-first/protocol/openid-connect/token"
    ai_engine_client_id: str = "reading-first-ocr-engine"
    ai_engine_client_secret: SecretStr = SecretStr("")

    # ── Inbound token validation (BLOCKED ON Q-01) ───────────────────────────────
    # The V1 PDF never states how the inbound {ai-engine-bearer-token} is validated, which
    # client the Backend presents, or what audience to expect. Until Q-01 is answered,
    # `auth_enforce` defaults False so the rest of the integration can be developed; a
    # startup warning is emitted, and enabling it requires the allowlist to be populated.
    auth_enforce: bool = False
    jwks_url: str | None = None
    # NoDecode: pydantic-settings would otherwise JSON-decode these env values before
    # our validator runs, so a plain `A,B` env var would fail to parse.
    backend_allowed_client_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    jwt_algorithms: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["RS256"])

    # Fleet-wide provider budgets (T-5.7), as `provider=calls/seconds` specs -- e.g.
    # `PROVIDER_CALL_BUDGETS=gemini=10/86400`, which is the real Gemini free-tier ceiling
    # recorded in README.md:491. Empty by default *on purpose*: a wrong low default would
    # silently park real books. See docs/03-configuration.md.
    provider_call_budgets: Annotated[list[str], NoDecode] = Field(default_factory=list)
    jwt_clock_leeway_seconds: int = 30
    # Keycloak's issuer is the realm URL. Derived from TOKEN_URL when unset.
    jwt_issuer: str | None = None
    # Optional: Keycloak client_credentials tokens do not reliably carry `aud`, so
    # requiring one would reject valid tokens. Identity is taken from `azp` instead.
    jwt_audience: str | None = None

    # ── Job store ────────────────────────────────────────────────────────────────
    # `sqlite` is durable and restart-safe but single-host (SQLite serializes writers).
    # `memory` is for tests and throwaway local runs only -- a restart loses in-flight
    # jobs. A Postgres backend is T-4.1 proper; see adapters/persistence/sql.py.
    job_store: str = "sqlite"
    job_store_path: Path = Path("/var/lib/ai-engine/jobs.db")

    # ── Storage ──────────────────────────────────────────────────────────────────
    scratch_dir: Path = Path("/var/tmp/ai-engine")
    model_cache_dir: Path = Path("/var/cache/ai-engine/models")
    # Paid-call cache (spec 021). Unset = no cache. Set it to reuse validated OCR and TTS
    # results on retried or re-run pages instead of paying for them again.
    provider_cache_dir: Path | None = None
    provider_cache_max_age_seconds: float | None = None
    max_source_bytes: int = 209_715_200  # 200 MB
    max_source_pages: int = 500

    # ── Security: SSRF control (required, not optional) ───────────────────────────
    # `sourceDownloadUrl` is caller-supplied and fetched server-side, so an unrestricted
    # engine is an open proxy into whatever network the worker sits in.
    source_url_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    source_url_require_https: bool = True
    # Resolve the host and reject loopback/link-local/private/reserved addresses. ON by
    # default: it is the only control that survives a DNS entry pointing somewhere it
    # should not, and link-local (169.254.169.254) is where cloud instance metadata lives.
    # Turn it off ONLY to point a worker at a fake Backend on localhost.
    source_url_resolve_and_check: bool = True

    # ── Concurrency, deadlines, retries ──────────────────────────────────────────
    worker_job_concurrency: int = 2
    worker_page_concurrency: int = 4
    job_page_concurrency: int = 4
    worker_lease_seconds: int = 300
    page_task_timeout_seconds: int = 900
    job_deadline_seconds: int = 86_400
    page_max_attempts: int = 3
    job_max_attempts: int = 3
    callback_max_attempts: int = 10
    reconcile_interval_seconds: int = 60
    retry_max_attempts: int = 5
    retry_base_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 60.0
    retry_total_budget_seconds: float = 120.0
    http_connect_timeout_seconds: float = 5.0
    http_read_timeout_seconds: float = 60.0

    # ── Feature flags ────────────────────────────────────────────────────────────
    default_quality_tier: QualityTier = QualityTier.FAST
    # Hard kill-switch. The verified tier is 65-200 paid calls per page against the fast
    # tier's 1-2, so a job asking for it is DOWNGRADED (and logged) while this is False.
    enable_verified_tier: bool = False
    tts_narrator: str = "female"
    alignment_engine: str = "whisper"
    tts_engine: str = "gemini"
    ocr_engine: str = "auto"  # auto | textlayer | gemini | lighton
    # Job A diacritisation (spec 016). Opt-in: "local" needs the arabic-diacritizer model
    # (torch, CPU) in the image; the worker refuses to start if it is set and the model
    # can't load, rather than failing every undiacritised page one by one.
    diacritizer: str = "none"  # none | local

    # ── Model configuration (was hardcoded constants in the legacy pipeline) ─────
    ocr_gemini_model: str = "gemini-3.6-flash"
    tts_gemini_model: str = "gemini-3.1-flash-tts-preview"
    tts_gemini_voice: str = "Kore"
    align_whisper_model_size: str = "small"
    align_whisper_device: str = "cpu"  # no GPU is required anywhere in V1
    align_whisper_compute_type: str = "int8"
    diac_threshold: float = 0.15
    gemini_api_key: SecretStr = SecretStr("")
    # The hosted host. The legacy default (http://localhost:8000/v1) plus the
    # client's /api/v3/parse suffix builds a nonsense URL and is set in no .env.
    lighton_base_url: str = "https://api.lighton.ai"
    lighton_api_key: SecretStr = SecretStr("")

    # ── Legacy pipeline location ─────────────────────────────────────────────────
    # Adapters -- and only adapters -- add this to sys.path to import `pipeline`.
    legacy_repo_root: Path | None = None

    @field_validator("provider_call_budgets")
    @classmethod
    def _parsable_budgets(cls, value: list[str]) -> list[str]:
        """Reject a malformed budget spec at boot rather than on the first paid call."""
        parse_limits(value)
        return value

    def provider_limits(self) -> dict[str, ProviderLimit]:
        """Configured budgets, keyed by provider. Empty means nothing is metered."""
        return parse_limits(self.provider_call_budgets)

    @field_validator("ocr_engine")
    @classmethod
    def _known_ocr_engine(cls, value: str) -> str:
        """Catch a typo'd OCR_ENGINE at boot, not on the first job."""
        if value not in OCR_ENGINES:
            raise ValueError(f"must be one of {sorted(OCR_ENGINES)}, got {value!r}")
        return value

    @field_validator("diacritizer")
    @classmethod
    def _known_diacritizer(cls, value: str) -> str:
        if value not in DIACRITIZERS:
            raise ValueError(f"must be one of {sorted(DIACRITIZERS)}, got {value!r}")
        return value

    @field_validator("backend_base_url", "token_url")
    @classmethod
    def _absolute_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"must be an absolute http(s) URL, got {value!r}")
        return value.rstrip("/")

    @field_validator(
        "backend_allowed_client_ids",
        "source_url_allowed_hosts",
        "jwt_algorithms",
        "provider_call_budgets",
        mode="before",
    )
    @classmethod
    def _parse_list(cls, value: object) -> object:
        """Accept a real list, a comma-separated env string, or a JSON array.

        These fields are annotated ``NoDecode`` so that pydantic-settings does not
        JSON-decode the raw env value before this runs -- without that, the ordinary
        ``HOSTS=a.example.com,b.example.com`` form raises a parse error. Handling both
        forms here keeps CSV (what .env.example documents) working while still accepting
        a JSON array from an orchestrator that emits one.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            import json

            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"looks like JSON but does not parse: {exc}") from exc
            if not isinstance(decoded, list):
                raise ValueError("JSON value must be an array")
            return [str(item).strip() for item in decoded if str(item).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]

    @field_validator("jwt_algorithms")
    @classmethod
    def _asymmetric_only(cls, value: list[str]) -> list[str]:
        """Reject symmetric algorithms outright.

        Accepting HS256 alongside a JWKS-based verifier is the classic algorithm-confusion
        vulnerability: the public key becomes a valid HMAC secret.
        """
        bad = [alg for alg in value if not alg.startswith(("RS", "ES", "PS"))]
        if bad:
            raise ValueError(f"only asymmetric JWT algorithms are permitted, refusing {bad}")
        return value

    @field_validator("job_store")
    @classmethod
    def _known_store(cls, value: str) -> str:
        allowed = {"memory", "sqlite"}
        if value not in allowed:
            raise ValueError(f"job_store must be one of {sorted(allowed)}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _check_enforced_auth_is_configured(self) -> Settings:
        if self.auth_enforce and not self.backend_allowed_client_ids:
            raise ValueError(
                "auth_enforce=True requires backend_allowed_client_ids to be set "
                "(see Q-01: the Backend team must tell us which Keycloak client they "
                "present)"
            )
        return self

    @property
    def resolved_jwks_url(self) -> str:
        """Derive the realm's JWKS endpoint from the token endpoint if not set explicitly."""
        if self.jwks_url:
            return self.jwks_url
        if self.token_url.endswith("/token"):
            return self.token_url[: -len("/token")] + "/certs"
        return self.token_url + "/certs"

    def require_secrets(self) -> None:
        """Fail fast on secrets that must be present for outbound calls to work.

        Called by the worker entrypoint, not the API -- the API makes no outbound Backend
        calls, so it can start and serve health checks without the client secret.
        """
        missing = [
            name
            for name, value in (
                ("AI_ENGINE_CLIENT_SECRET", self.ai_engine_client_secret.get_secret_value()),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("missing required configuration: " + ", ".join(missing))


def assert_no_dotenv_on_disk(start: Path | None = None) -> None:
    """Refuse to run if a `.env` file is present.

    The legacy modules this service imports call ``load_dotenv()`` at import time, so a
    `.env` on disk would silently shadow injected secrets. Failing loudly here is the only
    way to make that impossible rather than merely unlikely.
    """
    root = start or Path.cwd()
    candidate = root / ".env"
    if candidate.exists():
        raise RuntimeError(
            f"refusing to start: {candidate} exists. Legacy modules call load_dotenv() at "
            "import, so a .env on disk can silently override injected secrets. Remove it "
            "and inject configuration through the environment."
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, constructed once.

    Cached for convenience at the process edge (entrypoint, FastAPI dependency), not
    imported as a module global. Tests clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings()


def settings_from_env(**overrides: object) -> Settings:
    """Build settings with explicit overrides. Used by tests and by the entrypoints."""
    merged = {**{k.lower(): v for k, v in os.environ.items()}, **overrides}
    return Settings(**merged)  # type: ignore[arg-type]
