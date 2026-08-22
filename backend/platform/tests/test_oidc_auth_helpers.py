from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from platform_api.auth import (
    JwtAuthenticationError,
    OidcUnknownKeyId,
    strict_json_object,
    verify_oidc_id_token,
)


ISSUER = "https://identity.example.test"
AUDIENCE = "ai-video-browser"
NONCE = "expected-browser-nonce"
NOW_TIMESTAMP = 1_787_200_000
NOW = datetime.fromtimestamp(NOW_TIMESTAMP, timezone.utc)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = "key-1",
) -> dict:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "key_ops": ["verify"],
        "alg": "RS256",
        "kid": kid,
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _payload(**overrides: object) -> dict:
    payload = {
        "iss": ISSUER,
        "sub": "external-subject-1",
        "aud": AUDIENCE,
        "exp": NOW_TIMESTAMP + 300,
        "iat": NOW_TIMESTAMP,
        "nonce": NONCE,
        "email": "Person@Example.com",
        "email_verified": True,
        "name": "Test Person",
        "amr": ["webauthn"],
        "auth_time": NOW_TIMESTAMP - 10,
    }
    payload.update(overrides)
    return payload


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    payload: dict | None = None,
    header: dict | None = None,
    raw_payload: str | None = None,
    raw_header: str | None = None,
) -> str:
    protected = header or {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
    encoded_header = _b64(
        (
            raw_header
            if raw_header is not None
            else json.dumps(protected, separators=(",", ":"), sort_keys=True)
        ).encode("utf-8")
    )
    encoded_payload = _b64(
        (
            raw_payload
            if raw_payload is not None
            else json.dumps(
                payload if payload is not None else _payload(),
                separators=(",", ":"),
                sort_keys=True,
            )
        ).encode("utf-8")
    )
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = private_key.sign(
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{signing_input.decode('ascii')}.{_b64(signature)}"


def _verify(
    token: str,
    private_key: rsa.RSAPrivateKey,
    *,
    jwks: dict | None = None,
):
    return verify_oidc_id_token(
        token,
        jwks=jwks if jwks is not None else {"keys": [_jwk(private_key)]},
        issuer=ISSUER,
        audience=AUDIENCE,
        nonce=NONCE,
        now=NOW,
        clock_skew_seconds=30,
        maximum_lifetime_seconds=900,
    )


def test_rs256_id_token_happy_path_and_missing_auth_time(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    claims = _verify(_token(signing_key), signing_key)
    assert claims.issuer == ISSUER
    assert claims.subject == "external-subject-1"
    assert claims.email == "person@example.com"
    assert claims.authentication_methods == ("webauthn",)
    assert claims.authentication_time == NOW_TIMESTAMP - 10

    without_auth_time = _payload()
    without_auth_time.pop("auth_time")
    claims = _verify(_token(signing_key, payload=without_auth_time), signing_key)
    assert claims.authentication_time is None


def test_authentication_time_cannot_follow_issue_time_beyond_clock_skew(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    issued_at = NOW_TIMESTAMP - 300
    boundary = _verify(
        _token(
            signing_key,
            payload=_payload(
                iat=issued_at,
                auth_time=issued_at + 30,
            ),
        ),
        signing_key,
    )
    assert boundary.authentication_time == issued_at + 30

    with pytest.raises(JwtAuthenticationError, match="authentication time"):
        _verify(
            _token(
                signing_key,
                payload=_payload(
                    iat=issued_at,
                    auth_time=issued_at + 31,
                ),
            ),
            signing_key,
        )


def test_multiple_audiences_require_exact_azp(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    claims = _verify(
        _token(
            signing_key,
            payload=_payload(aud=[AUDIENCE, "secondary-api"], azp=AUDIENCE),
        ),
        signing_key,
    )
    assert claims.subject == "external-subject-1"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"iss": "https://wrong.example.test"}, "issuer"),
        ({"aud": "another-client"}, "audience"),
        ({"aud": [AUDIENCE, "secondary-api"]}, "authorized party"),
        (
            {"aud": [AUDIENCE, "secondary-api"], "azp": "secondary-api"},
            "authorized party",
        ),
        ({"aud": [AUDIENCE, AUDIENCE], "azp": AUDIENCE}, "audience"),
        ({"aud": AUDIENCE, "azp": "another-client"}, "authorized party"),
        ({"nonce": "wrong-nonce"}, "nonce"),
        ({"sub": ""}, "subject"),
        ({"sub": " whitespace "}, "subject"),
        ({"exp": NOW_TIMESTAMP - 31}, "expired"),
        ({"iat": NOW_TIMESTAMP + 31}, "issue time"),
        ({"exp": NOW_TIMESTAMP + 901}, "lifetime"),
        ({"email_verified": False}, "not verified"),
        ({"auth_time": NOW_TIMESTAMP + 31}, "authentication time"),
    ],
)
def test_oidc_claim_contract_rejects_invalid_values(
    signing_key: rsa.RSAPrivateKey,
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(JwtAuthenticationError, match=message):
        _verify(_token(signing_key, payload=_payload(**overrides)), signing_key)


def test_unknown_kid_has_a_distinct_refresh_signal(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    token = _token(
        signing_key,
        header={"alg": "RS256", "kid": "rotated-key", "typ": "JWT"},
    )
    with pytest.raises(OidcUnknownKeyId, match="unknown"):
        _verify(token, signing_key)


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "HS256", "kid": "key-1", "typ": "JWT"},
        {"alg": "none", "kid": "key-1", "typ": "JWT"},
        {"alg": "RS256", "kid": "key-1", "typ": "at+jwt"},
        {"alg": "RS256", "kid": "key-1", "crit": ["exp"]},
        {"alg": "RS256", "typ": "JWT"},
    ],
)
def test_only_the_fixed_rs256_header_profile_is_accepted(
    signing_key: rsa.RSAPrivateKey,
    header: dict,
) -> None:
    with pytest.raises(JwtAuthenticationError):
        _verify(_token(signing_key, header=header), signing_key)


@pytest.mark.parametrize(
    "jwk_mutation",
    [
        {"kty": "EC"},
        {"use": "enc"},
        {"key_ops": ["sign"]},
        {"alg": "RS512"},
        {"n": "not+base64url"},
        {"e": "not+base64url"},
    ],
)
def test_jwks_key_profile_is_strict(
    signing_key: rsa.RSAPrivateKey,
    jwk_mutation: dict[str, object],
) -> None:
    jwk = {**_jwk(signing_key), **jwk_mutation}
    with pytest.raises(JwtAuthenticationError):
        _verify(_token(signing_key), signing_key, jwks={"keys": [jwk]})


def test_duplicate_kid_and_small_rsa_key_are_rejected(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    jwk = _jwk(signing_key)
    with pytest.raises(JwtAuthenticationError, match="ambiguous"):
        _verify(
            _token(signing_key),
            signing_key,
            jwks={"keys": [jwk, deepcopy(jwk)]},
        )

    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    with pytest.raises(JwtAuthenticationError, match="too small"):
        _verify(
            _token(small),
            small,
            jwks={"keys": [_jwk(small)]},
        )


@pytest.mark.parametrize("segment_index", [0, 1, 2])
def test_noncanonical_base64url_segments_are_rejected(
    signing_key: rsa.RSAPrivateKey,
    segment_index: int,
) -> None:
    parts = _token(signing_key).split(".")
    parts[segment_index] += "="
    with pytest.raises(JwtAuthenticationError):
        _verify(".".join(parts), signing_key)


def test_duplicate_json_members_are_rejected_before_signature_trust(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    raw_payload = json.dumps(_payload(), separators=(",", ":"))
    raw_payload = raw_payload[:-1] + f',"iss":"{ISSUER}"' + "}"
    with pytest.raises(JwtAuthenticationError):
        _verify(_token(signing_key, raw_payload=raw_payload), signing_key)

    raw_header = '{"alg":"RS256","kid":"key-1","kid":"key-1"}'
    with pytest.raises(JwtAuthenticationError):
        _verify(_token(signing_key, raw_header=raw_header), signing_key)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_numeric_dates_are_rejected(
    signing_key: rsa.RSAPrivateKey,
    constant: str,
) -> None:
    raw_payload = json.dumps(_payload(), separators=(",", ":"))
    raw_payload = raw_payload.replace(
        f'"iat":{NOW_TIMESTAMP}',
        f'"iat":{constant}',
    )
    assert constant in raw_payload
    with pytest.raises(JwtAuthenticationError):
        _verify(_token(signing_key, raw_payload=raw_payload), signing_key)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"value":1,"value":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b"[]",
    ],
)
def test_provider_json_parser_rejects_duplicate_nonfinite_and_nonobject_json(
    raw: bytes,
) -> None:
    with pytest.raises(JwtAuthenticationError):
        strict_json_object(raw)


def test_signature_and_jwks_container_validation_are_fail_closed(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    token = _token(signing_key)
    encoded_header, encoded_payload, _ = token.split(".")
    forged = f"{encoded_header}.{encoded_payload}.{_b64(b'forged-signature')}"
    with pytest.raises(JwtAuthenticationError, match="signature"):
        _verify(forged, signing_key)
    for jwks in ({}, {"keys": "not-a-list"}, {"keys": ["not-an-object"]}):
        with pytest.raises((JwtAuthenticationError, OidcUnknownKeyId)):
            _verify(token, signing_key, jwks=jwks)
