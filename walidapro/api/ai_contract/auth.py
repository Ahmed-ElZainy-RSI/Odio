from __future__ import annotations

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from .config import AI_CONTRACT_SETTINGS as SETTINGS

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client

    if _jwks_client is None:
        if not SETTINGS.jwt_issuer:
            raise HTTPException(
                503,
                "Token validation not configured: TOKEN_URL / AI_ENGINE_JWT_ISSUER is empty.",
            )
        jwks_url = f"{SETTINGS.jwt_issuer}/protocol/openid-connect/certs"
        _jwks_client = PyJWKClient(jwks_url)

    return _jwks_client


def verify_ai_engine_token(authorization: str | None = Header(None)) -> dict:
    """
    FastAPI dependency: validates the Bearer token the backend attaches
    when it calls `POST /api/jobs`.

    Verifies signature (via the realm's JWKS), issuer, and expiry.
    Audience is only checked if AI_ENGINE_JWT_AUDIENCE is set, since
    Keycloak client_credentials tokens don't always carry a predictable
    audience claim by default.

    Set AI_ENGINE_AUTH_DISABLED=true for local development without a
    live Keycloak instance -- disabled by default is NOT the default;
    validation is on unless explicitly turned off.
    """
    if SETTINGS.auth_disabled:
        return {"sub": "dev-mode", "auth_disabled": True}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header.")

    token = authorization.split(" ", 1)[1].strip()

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)

        decode_kwargs = {"algorithms": ["RS256"], "issuer": SETTINGS.jwt_issuer}
        if SETTINGS.jwt_audience:
            decode_kwargs["audience"] = SETTINGS.jwt_audience
        else:
            decode_kwargs["options"] = {"verify_aud": False}

        claims = jwt.decode(token, signing_key.key, **decode_kwargs)

    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "Token expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, f"Invalid token: {exc}") from exc
    except Exception as exc:
        raise HTTPException(503, f"Token validation unavailable: {exc}") from exc

    return claims
