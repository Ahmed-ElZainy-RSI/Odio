from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _derive_issuer(token_url: str) -> str:
    """
    Keycloak token_url looks like:
        http://host:port/realms/<realm>/protocol/openid-connect/token
    The JWT issuer (and JWKS base) is everything before "/protocol/...".
    """
    marker = "/protocol/openid-connect/token"
    if token_url.endswith(marker):
        return token_url[: -len(marker)]
    return token_url.rstrip("/")


@dataclass(frozen=True)
class AIContractSettings:
    # --- Backend (the service AI Engine calls INTO) ---
    backend_base_url: str = os.getenv("BACKEND_BASE_URL", "").rstrip("/")

    # --- Keycloak client_credentials (used both to call the backend AND to
    # validate tokens the backend attaches when it calls AI Engine) ---
    token_url: str = os.getenv("TOKEN_URL", "")
    client_id: str = os.getenv("AI_ENGINE_CLIENT_ID", "reading-first-ocr-engine")
    client_secret: str = os.getenv("AI_ENGINE_CLIENT_SECRET", "")

    # --- Incoming-token validation (AI Engine acting as resource server) ---
    # Issuer/JWKS default to being derived from TOKEN_URL; override explicitly
    # if the backend's tokens come from a different realm/gateway.
    jwt_issuer: str = os.getenv("AI_ENGINE_JWT_ISSUER", "") or _derive_issuer(
        os.getenv("TOKEN_URL", "")
    )
    jwt_audience: str = os.getenv("AI_ENGINE_JWT_AUDIENCE", "")  # optional

    # Dev-only escape hatch: no Keycloak/backend is live yet, so this lets the
    # endpoint be exercised locally. Defaults to secure (validation ON).
    auth_disabled: bool = os.getenv("AI_ENGINE_AUTH_DISABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    # --- Fields the job payload does not specify but processing needs ---
    # Not in the spec's job body; the backend team should confirm/override.
    default_narrator: str = os.getenv("AI_ENGINE_DEFAULT_NARRATOR", "female")
    default_dpi: int = int(os.getenv("AI_ENGINE_DEFAULT_DPI", "220"))

    # HTTP behavior
    http_timeout: int = int(os.getenv("AI_ENGINE_HTTP_TIMEOUT", "120"))
    token_expiry_safety_seconds: int = int(
        os.getenv("AI_ENGINE_TOKEN_SAFETY_SECONDS", "30")
    )


AI_CONTRACT_SETTINGS = AIContractSettings()
