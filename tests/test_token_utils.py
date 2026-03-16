import time

import pytest

from token_utils import TokenError, decode_hs256_jwt, encode_hs256_jwt


def test_encode_and_decode_round_trip():
    payload = {
        "iss": "issuer",
        "aud": "audience",
        "sub": "user-1",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }

    token = encode_hs256_jwt(payload, "secret-1")
    decoded = decode_hs256_jwt(token, "secret-1", audience="audience", issuer="issuer")

    assert decoded["sub"] == "user-1"


def test_decode_rejects_invalid_signature():
    payload = {"sub": "user-1", "exp": int(time.time()) + 60}
    token = encode_hs256_jwt(payload, "secret-1")

    with pytest.raises(TokenError, match="Invalid token signature"):
        decode_hs256_jwt(token, "secret-2")


def test_decode_rejects_expired_tokens():
    payload = {"sub": "user-1", "exp": int(time.time()) - 1}
    token = encode_hs256_jwt(payload, "secret-1")

    with pytest.raises(TokenError, match="Token expired"):
        decode_hs256_jwt(token, "secret-1")


def test_decode_rejects_wrong_audience():
    payload = {"sub": "user-1", "aud": "expected", "exp": int(time.time()) + 60}
    token = encode_hs256_jwt(payload, "secret-1")

    with pytest.raises(TokenError, match="Invalid token audience"):
        decode_hs256_jwt(token, "secret-1", audience="other")


def test_decode_rejects_tokens_before_nbf():
    payload = {"sub": "user-1", "nbf": int(time.time()) + 60, "exp": int(time.time()) + 120}
    token = encode_hs256_jwt(payload, "secret-1")

    with pytest.raises(TokenError, match="Token not active"):
        decode_hs256_jwt(token, "secret-1")
