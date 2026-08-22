from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes
from email_validator import EmailNotValidError, validate_email


class JwtAuthenticationError(ValueError):
    """Raised when a bearer token cannot be trusted."""


class OidcUnknownKeyId(JwtAuthenticationError):
    """Raised so the caller can refresh a cached JWKS exactly once."""


@dataclass(frozen=True)
class JwtPrincipal:
    user_id: str
    issuer: str
    company_id: str | None
    platform_admin: bool
    authentication_time: float | None
    authentication_methods: tuple[str, ...]


@dataclass(frozen=True)
class OidcIdTokenClaims:
    issuer: str
    subject: str
    email: str
    display_name: str
    issued_at: float
    expires_at: float
    authentication_time: float | None
    authentication_methods: tuple[str, ...]


_BASE64URL_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def strict_json_object(raw: bytes, *, maximum_bytes: int = 1_048_576) -> dict[str, Any]:
    if not raw or len(raw) > maximum_bytes:
        raise JwtAuthenticationError("OIDC JSON response is malformed")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise JwtAuthenticationError("OIDC JSON response is malformed") from exc
    if not isinstance(value, dict):
        raise JwtAuthenticationError("OIDC JSON response is malformed")
    return value


def normalize_email_address(value: str) -> str:
    try:
        normalized = validate_email(
            value, check_deliverability=False
        ).normalized
    except (EmailNotValidError, TypeError) as exc:
        raise JwtAuthenticationError("OIDC ID token email is invalid") from exc
    if len(normalized) > 320:
        raise JwtAuthenticationError("OIDC ID token email is invalid")
    return normalized.lower()


def _decode_segment(value: str) -> bytes:
    if not value or not _BASE64URL_SEGMENT.fullmatch(value):
        raise JwtAuthenticationError("Malformed bearer token")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(
            (value + padding).encode("ascii"), altchars=b"-_", validate=True
        )
        if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
            raise JwtAuthenticationError("Malformed bearer token")
        return decoded
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise JwtAuthenticationError("Malformed bearer token") from exc


def _decode_json_segment(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(
            _decode_segment(value),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise JwtAuthenticationError("Malformed bearer token") from exc
    if not isinstance(decoded, dict):
        raise JwtAuthenticationError("Malformed bearer token")
    return decoded


def _numeric_date(payload: dict[str, Any], name: str, *, required: bool) -> float | None:
    value = payload.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JwtAuthenticationError(f"Bearer token has invalid {name}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise JwtAuthenticationError(f"Bearer token has invalid {name}")
    return numeric


def verify_hs256_jwt(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
    now: datetime | None = None,
    clock_skew_seconds: int = 30,
) -> JwtPrincipal:
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise JwtAuthenticationError("Malformed bearer token")
    encoded_header, encoded_payload, encoded_signature = parts
    header = _decode_json_segment(encoded_header)
    payload = _decode_json_segment(encoded_payload)
    if header.get("alg") != "HS256" or header.get("typ", "JWT") != "JWT":
        raise JwtAuthenticationError("Bearer token algorithm is not allowed")

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    supplied_signature = _decode_segment(encoded_signature)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise JwtAuthenticationError("Bearer token signature is invalid")

    current = (now or datetime.now(timezone.utc)).timestamp()
    expires_at = _numeric_date(payload, "exp", required=True)
    not_before = _numeric_date(payload, "nbf", required=False)
    issued_at = _numeric_date(payload, "iat", required=False)
    assert expires_at is not None
    if current - clock_skew_seconds >= expires_at:
        raise JwtAuthenticationError("Bearer token has expired")
    if not_before is not None and current + clock_skew_seconds < not_before:
        raise JwtAuthenticationError("Bearer token is not active")
    if issued_at is not None and current + clock_skew_seconds < issued_at:
        raise JwtAuthenticationError("Bearer token issue time is invalid")
    if payload.get("iss") != issuer:
        raise JwtAuthenticationError("Bearer token issuer is invalid")
    token_audience = payload.get("aud")
    audience_matches = token_audience == audience or (
        isinstance(token_audience, list)
        and all(isinstance(item, str) for item in token_audience)
        and audience in token_audience
    )
    if not audience_matches:
        raise JwtAuthenticationError("Bearer token audience is invalid")

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise JwtAuthenticationError("Bearer token subject is invalid")
    company_id = payload.get("company_id")
    if company_id is not None and (
        not isinstance(company_id, str) or not company_id.strip()
    ):
        raise JwtAuthenticationError("Bearer token company is invalid")
    platform_admin = payload.get("platform_admin", False)
    if not isinstance(platform_admin, bool):
        raise JwtAuthenticationError("Bearer token admin claim is invalid")
    authentication_time = _numeric_date(payload, "auth_time", required=False)
    authentication_methods_value = payload.get("amr", [])
    if not isinstance(authentication_methods_value, list) or not all(
        isinstance(item, str) and item.strip()
        for item in authentication_methods_value
    ):
        raise JwtAuthenticationError("Bearer token authentication methods are invalid")
    return JwtPrincipal(
        user_id=user_id,
        issuer=issuer,
        company_id=company_id,
        platform_admin=platform_admin,
        authentication_time=authentication_time,
        authentication_methods=tuple(authentication_methods_value),
    )


def _rsa_verification_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise JwtAuthenticationError("OIDC signing key type is not allowed")
    if jwk.get("use") not in (None, "sig"):
        raise JwtAuthenticationError("OIDC signing key use is not allowed")
    key_ops = jwk.get("key_ops")
    if key_ops is not None and (
        not isinstance(key_ops, list)
        or not all(isinstance(item, str) for item in key_ops)
        or "verify" not in key_ops
    ):
        raise JwtAuthenticationError("OIDC signing key operations are not allowed")
    if jwk.get("alg") not in (None, "RS256"):
        raise JwtAuthenticationError("OIDC signing key algorithm is not allowed")
    modulus = jwk.get("n")
    exponent = jwk.get("e")
    if not isinstance(modulus, str) or not isinstance(exponent, str):
        raise JwtAuthenticationError("OIDC signing key is malformed")
    try:
        numbers = rsa.RSAPublicNumbers(
            int.from_bytes(_decode_segment(exponent), "big"),
            int.from_bytes(_decode_segment(modulus), "big"),
        )
        key = numbers.public_key()
    except (ValueError, TypeError) as exc:
        raise JwtAuthenticationError("OIDC signing key is malformed") from exc
    if key.key_size < 2048:
        raise JwtAuthenticationError("OIDC signing key is too small")
    return key


def verify_oidc_id_token(
    token: str,
    *,
    jwks: dict[str, Any],
    issuer: str,
    audience: str,
    nonce: str,
    now: datetime | None = None,
    clock_skew_seconds: int = 30,
    maximum_lifetime_seconds: int = 900,
) -> OidcIdTokenClaims:
    """Verify the fixed OIDC ID-token profile used by the public PKCE client.

    Network retrieval is deliberately outside this function. Callers fetch the
    server-configured JWKS URI and may retry once on ``OidcUnknownKeyId``.
    """

    if not isinstance(token, str) or len(token) > 32_768:
        raise JwtAuthenticationError("OIDC ID token is malformed")
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise JwtAuthenticationError("OIDC ID token is malformed")
    encoded_header, encoded_payload, encoded_signature = parts
    header = _decode_json_segment(encoded_header)
    payload = _decode_json_segment(encoded_payload)
    if header.get("alg") != "RS256" or header.get("typ", "JWT") != "JWT":
        raise JwtAuthenticationError("OIDC ID token algorithm is not allowed")
    if header.get("crit") not in (None, []):
        raise JwtAuthenticationError("OIDC ID token critical headers are unsupported")
    key_id = header.get("kid")
    if not isinstance(key_id, str) or not key_id or len(key_id) > 256:
        raise JwtAuthenticationError("OIDC ID token key id is invalid")
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise JwtAuthenticationError("OIDC JWKS is malformed")
    candidates = [
        item
        for item in keys
        if isinstance(item, dict) and item.get("kid") == key_id
    ]
    if not candidates:
        raise OidcUnknownKeyId("OIDC signing key is unknown")
    if len(candidates) != 1:
        raise JwtAuthenticationError("OIDC signing key id is ambiguous")
    verification_key = _rsa_verification_key(candidates[0])
    try:
        verification_key.verify(
            _decode_segment(encoded_signature),
            f"{encoded_header}.{encoded_payload}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise JwtAuthenticationError("OIDC ID token signature is invalid") from exc

    current = (now or datetime.now(timezone.utc)).timestamp()
    expires_at = _numeric_date(payload, "exp", required=True)
    issued_at = _numeric_date(payload, "iat", required=True)
    not_before = _numeric_date(payload, "nbf", required=False)
    assert expires_at is not None and issued_at is not None
    if current - clock_skew_seconds >= expires_at:
        raise JwtAuthenticationError("OIDC ID token has expired")
    if issued_at > current + clock_skew_seconds:
        raise JwtAuthenticationError("OIDC ID token issue time is invalid")
    if expires_at <= issued_at or expires_at - issued_at > maximum_lifetime_seconds:
        raise JwtAuthenticationError("OIDC ID token lifetime is invalid")
    if not_before is not None and current + clock_skew_seconds < not_before:
        raise JwtAuthenticationError("OIDC ID token is not active")
    if payload.get("iss") != issuer:
        raise JwtAuthenticationError("OIDC ID token issuer is invalid")

    token_audience = payload.get("aud")
    if isinstance(token_audience, str):
        audiences = [token_audience]
    elif isinstance(token_audience, list) and token_audience and all(
        isinstance(item, str) and item for item in token_audience
    ):
        audiences = token_audience
    else:
        raise JwtAuthenticationError("OIDC ID token audience is invalid")
    if audience not in audiences:
        raise JwtAuthenticationError("OIDC ID token audience is invalid")
    if len(set(audiences)) != len(audiences):
        raise JwtAuthenticationError("OIDC ID token audience is invalid")
    authorized_party = payload.get("azp")
    if len(audiences) > 1 and authorized_party != audience:
        raise JwtAuthenticationError("OIDC ID token authorized party is invalid")
    if authorized_party is not None and authorized_party != audience:
        raise JwtAuthenticationError("OIDC ID token authorized party is invalid")
    if payload.get("nonce") != nonce:
        raise JwtAuthenticationError("OIDC ID token nonce is invalid")

    subject = payload.get("sub")
    email = payload.get("email")
    if (
        not isinstance(subject, str)
        or not subject.strip()
        or subject != subject.strip()
        or len(subject) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in subject)
    ):
        raise JwtAuthenticationError("OIDC ID token subject is invalid")
    if (
        not isinstance(email, str)
        or not email.strip()
        or email != email.strip()
        or len(email) > 320
        or any(ord(character) < 32 or ord(character) == 127 for character in email)
    ):
        raise JwtAuthenticationError("OIDC ID token email is invalid")
    if payload.get("email_verified") is not True:
        raise JwtAuthenticationError("OIDC ID token email is not verified")
    authentication_time = _numeric_date(payload, "auth_time", required=False)
    if authentication_time is not None and (
        authentication_time > current + clock_skew_seconds
        or authentication_time > issued_at + clock_skew_seconds
    ):
        raise JwtAuthenticationError("OIDC ID token authentication time is invalid")
    amr_value = payload.get("amr", [])
    if not isinstance(amr_value, list) or not all(
        isinstance(item, str) and item.strip() and len(item) <= 80
        for item in amr_value
    ):
        raise JwtAuthenticationError("OIDC ID token authentication methods are invalid")
    display_name_value = (
        payload.get("name") or payload.get("preferred_username") or email
    )
    if not isinstance(display_name_value, str) or not display_name_value.strip():
        display_name_value = email
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in display_name_value
    ):
        raise JwtAuthenticationError("OIDC ID token display name is invalid")
    display_name = display_name_value.strip()[:120].strip()
    if not display_name:
        raise JwtAuthenticationError("OIDC ID token display name is invalid")
    normalized_email = normalize_email_address(email.strip())
    return OidcIdTokenClaims(
        issuer=issuer,
        subject=subject,
        email=normalized_email,
        display_name=display_name,
        issued_at=issued_at,
        expires_at=expires_at,
        authentication_time=authentication_time,
        authentication_methods=tuple(amr_value),
    )


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise JwtAuthenticationError("Missing bearer token")
    scheme, separator, credentials = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not credentials
        or credentials != credentials.strip()
        or " " in credentials
    ):
        raise JwtAuthenticationError("Malformed authorization header")
    return credentials
