from __future__ import annotations

import hashlib
import os
import time
from functools import lru_cache
from typing import Any, Dict, Optional

from token_utils import TokenError, decode_hs256_jwt, encode_hs256_jwt


APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"


class AppleIdentityError(ValueError):
    """Raised when Apple identity verification fails."""


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development") == "production"


def _expected_bundle_id() -> str:
    bundle_id = os.getenv("APPLE_BUNDLE_ID", "").strip()
    if bundle_id:
        return bundle_id
    if _is_production():
        raise AppleIdentityError("APPLE_BUNDLE_ID is not configured")
    return "com.llmchesscoach.dev"


def _nonce_matches(claim_nonce: Optional[str], provided_nonce: Optional[str]) -> bool:
    if claim_nonce is None:
        return provided_nonce in (None, "")
    if not provided_nonce:
        return False
    hashed_nonce = hashlib.sha256(provided_nonce.encode("utf-8")).hexdigest()
    return claim_nonce in {provided_nonce, hashed_nonce}


@lru_cache(maxsize=1)
def _load_pyjwt_client() -> Any:
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is absent
        raise AppleIdentityError("PyJWT is required for production Apple auth verification") from exc

    return jwt.PyJWKClient(APPLE_JWKS_URL)


def verify_apple_identity_token(identity_token: str, nonce: Optional[str] = None) -> Dict[str, Any]:
    if not identity_token:
        raise AppleIdentityError("Missing Apple identity token")

    bundle_id = _expected_bundle_id()
    test_secret = os.getenv("APPLE_TEST_IDENTITY_SECRET", "").strip()
    if test_secret and not _is_production():
        claims = decode_hs256_jwt(identity_token, test_secret, audience=bundle_id, issuer=APPLE_ISSUER)
        if not _nonce_matches(claims.get("nonce"), nonce):
            raise AppleIdentityError("Apple nonce mismatch")
        if not claims.get("sub"):
            raise AppleIdentityError("Apple identity token missing subject")
        return claims

    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is absent
        raise AppleIdentityError("PyJWT is required for production Apple auth verification") from exc

    try:
        signing_key = _load_pyjwt_client().get_signing_key_from_jwt(identity_token)
        claims = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=bundle_id,
            issuer=APPLE_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except Exception as exc:
        raise AppleIdentityError("Apple identity token verification failed") from exc

    if not _nonce_matches(claims.get("nonce"), nonce):
        raise AppleIdentityError("Apple nonce mismatch")
    if not claims.get("sub"):
        raise AppleIdentityError("Apple identity token missing subject")
    return claims


def build_test_identity_token(
    apple_sub: str,
    bundle_id: str,
    secret: str,
    nonce: Optional[str] = None,
    email: Optional[str] = None,
    ttl_seconds: int = 3600,
) -> str:
    now = int(os.getenv("APPLE_TEST_NOW", "0") or 0) or int(time.time())
    payload: Dict[str, Any] = {
        "iss": APPLE_ISSUER,
        "aud": bundle_id,
        "sub": apple_sub,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if nonce:
        payload["nonce"] = nonce
    if email:
        payload["email"] = email
    return encode_hs256_jwt(payload, secret)
