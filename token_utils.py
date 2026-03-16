from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


class TokenError(ValueError):
    """Raised when a token cannot be verified."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def encode_hs256_jwt(payload: Dict[str, Any], secret: str, header: Optional[Dict[str, Any]] = None) -> str:
    token_header = {"alg": "HS256", "typ": "JWT"}
    if header:
        token_header.update(header)

    encoded_header = _b64url_encode(json.dumps(token_header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url_encode(signature)}"


def decode_hs256_jwt(
    token: str,
    secret: str,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
    leeway_seconds: int = 0,
) -> Dict[str, Any]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
    except ValueError as exc:  # pragma: no cover - malformed tokens are covered by higher-level tests
        raise TokenError("Malformed token") from exc

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual_signature = _b64url_decode(encoded_signature)
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise TokenError("Invalid token signature")

    header = json.loads(_b64url_decode(encoded_header))
    if header.get("alg") != "HS256":
        raise TokenError("Unsupported token algorithm")

    payload = json.loads(_b64url_decode(encoded_payload))
    now = time.time()
    exp = payload.get("exp")
    if exp is not None and float(exp) < (now - leeway_seconds):
        raise TokenError("Token expired")
    nbf = payload.get("nbf")
    if nbf is not None and float(nbf) > (now + leeway_seconds):
        raise TokenError("Token not active")
    if issuer is not None and payload.get("iss") != issuer:
        raise TokenError("Invalid token issuer")
    if audience is not None:
        raw_aud = payload.get("aud")
        valid = False
        if isinstance(raw_aud, str):
            valid = raw_aud == audience
        elif isinstance(raw_aud, list):
            valid = audience in raw_aud
        if not valid:
            raise TokenError("Invalid token audience")
    return payload
