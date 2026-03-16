from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from entitlements import EntitlementStore, UserRecord
from token_utils import TokenError, decode_hs256_jwt, encode_hs256_jwt


BACKEND_TOKEN_TTL_SECONDS = 24 * 60 * 60
BACKEND_TOKEN_ISSUER = "llm-chesscoach"


class AuthConfigurationError(RuntimeError):
    """Raised when auth configuration is invalid."""


class AuthError(ValueError):
    """Raised when a bearer token cannot be authenticated."""


@dataclass
class AuthContext:
    user_id: int
    apple_sub: str
    apple_email: Optional[str]
    is_development_override: bool = False


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development") == "production"


def _backend_secret() -> str:
    secret = os.getenv("BACKEND_AUTH_SECRET", "").strip()
    if secret:
        return secret
    if _is_production():
        raise AuthConfigurationError("BACKEND_AUTH_SECRET is not configured")
    return "dev-backend-auth-secret"


def issue_backend_token(user: UserRecord) -> str:
    now = int(time.time())
    payload = {
        "iss": BACKEND_TOKEN_ISSUER,
        "aud": "llm-chesscoach-api",
        "sub": str(user.id),
        "apple_sub": user.apple_sub,
        "email": user.apple_email,
        "iat": now,
        "exp": now + BACKEND_TOKEN_TTL_SECONDS,
    }
    return encode_hs256_jwt(payload, _backend_secret())


def authenticate_bearer_token(token: str, store: EntitlementStore) -> AuthContext:
    try:
        claims = decode_hs256_jwt(token, _backend_secret(), audience="llm-chesscoach-api", issuer=BACKEND_TOKEN_ISSUER)
    except TokenError as exc:
        raise AuthError("Invalid bearer token") from exc

    user = store.get_user_by_id(int(claims["sub"]))
    if not user:
        raise AuthError("Unknown user")
    return AuthContext(
        user_id=user.id,
        apple_sub=user.apple_sub,
        apple_email=user.apple_email,
        is_development_override=False,
    )


def authenticate_development_api_key(api_key: str, store: EntitlementStore) -> AuthContext:
    configured = os.getenv("API_KEY", "").strip()
    if _is_production() or not configured or api_key != configured:
        raise AuthError("Invalid API key")

    user = store.upsert_user("dev-api-key-user", apple_email="dev@example.com")
    return AuthContext(
        user_id=user.id,
        apple_sub=user.apple_sub,
        apple_email=user.apple_email,
        is_development_override=True,
    )
