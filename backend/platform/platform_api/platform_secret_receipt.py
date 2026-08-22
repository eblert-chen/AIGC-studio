from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

PLATFORM_SECRET_ISOLATION_RECEIPT_ENV = "RELAY_SECRET_ISOLATION_RECEIPT_FILE"
PLATFORM_SECRET_ISOLATION_COMMIT_ENV = "RELAY_SECRET_ISOLATION_COMMIT_FILE"
PLATFORM_SECRET_ISOLATION_RECEIPT_KIND = "relay_secret_isolation_commitment"
PLATFORM_SECRET_ISOLATION_RECEIPT_SCHEMA_VERSION = 2
PLATFORM_SECRET_ISOLATION_COMMIT_KIND = "relay_secret_isolation_commit"
PLATFORM_SECRET_ISOLATION_COMMIT_SCHEMA_VERSION = 2
PLATFORM_SECRET_ISOLATION_REQUIRED_GENERATION = "root-proof-present"
PLATFORM_RELEASE_IDENTITY_FILE = "/app/platform-release-identity.json"

_MAXIMUM_RECEIPT_BYTES = 1024 * 1024
_MAXIMUM_RELEASE_IDENTITY_BYTES = 4096
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_BARE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[0-9a-f]{64}$")
_PLATFORM_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$"
)
_COMMITMENT_ID = re.compile(r"^[a-z0-9][A-Za-z0-9._:-]{0,511}$")

_TOP_LEVEL_FIELD_ORDER = (
    "schema_version",
    "kind",
    "run_id",
    "consumer",
    "release",
    "files",
    "semantics",
)
_TOP_LEVEL_FIELDS = frozenset(_TOP_LEVEL_FIELD_ORDER)
_COMMIT_FIELD_ORDER = (
    "schema_version",
    "kind",
    "run_id",
    "generation",
    "root_proof_id",
    "release",
    "receipts",
)
_COMMIT_FIELDS = frozenset(_COMMIT_FIELD_ORDER)
_RELEASE_FIELD_ORDER = (
    "image_digest",
    "source_revision",
    "source_snapshot_sha256",
    "source_snapshot_file_count",
    "upstream_revision",
    "route_acceptance_trust_keys_sha256",
    "platform_image",
    "platform_source_revision",
    "platform_source_snapshot_sha256",
    "platform_origin",
    "relay_origin",
    "edge_origin",
    "relay_contract_revision",
)
_RELEASE_FIELDS = frozenset(_RELEASE_FIELD_ORDER)
_COMMITMENT_FIELDS = frozenset({"id", "sha256"})
_PLATFORM_IDENTITY_FIELDS = frozenset(
    {"platform_source_revision", "platform_source_snapshot_sha256"}
)
_RELEASE_ENVIRONMENT = {
    "image_digest": "RELAY_COMPAT_IMAGE_DIGEST",
    "source_revision": "RELAY_COMPAT_SOURCE_REVISION",
    "source_snapshot_sha256": "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256",
    "source_snapshot_file_count": "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT",
    "upstream_revision": "RELAY_COMPAT_UPSTREAM_REVISION",
    "route_acceptance_trust_keys_sha256": (
        "RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256"
    ),
    "platform_image": "PLATFORM_IMAGE",
    "platform_source_revision": "PLATFORM_SOURCE_REVISION",
    "platform_source_snapshot_sha256": "PLATFORM_SOURCE_SNAPSHOT_SHA256",
    "platform_origin": "PLATFORM_PUBLIC_BASE_URL",
    "relay_origin": "NEW_API_RELAY_PUBLIC_BASE_URL",
    "edge_origin": "DOWNLOAD_GATEWAY_PUBLIC_BASE_URL",
    "relay_contract_revision": "PLATFORM_NEW_API_RELAY_CONTRACT_REVISION",
}

_ALL_CONSUMERS = (
    "api",
    "edge",
    "migrate",
    "platform-api",
    "platform-db-role-pre",
    "platform-dispatcher",
    "platform-download-gateway-registration-worker",
    "platform-migration",
    "platform-publishing-worker",
    "platform-relay-sync",
    "platform-timeout-worker",
    "post",
    "pre",
    "principal",
)


class PlatformSecretIsolationReceiptError(RuntimeError):
    """A deliberately value-free global secret-isolation bootstrap failure."""


@dataclass(frozen=True)
class PlatformSecretIsolationContext:
    run_id: str
    generation: str
    root_proof_id: str
    platform_image: str
    platform_source_revision: str
    platform_source_snapshot_sha256: str


def _invalid(message: str = "Platform secret isolation receipt is invalid") -> None:
    raise PlatformSecretIsolationReceiptError(message)


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _strict_json_object(raw: bytes, *, allow_trailing_whitespace: bool) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _invalid()
    if not text or text.startswith("\ufeff") or text[0] != "{":
        _invalid()
    decoder = json.JSONDecoder(object_pairs_hook=_reject_duplicate_object)
    try:
        document, end = decoder.raw_decode(text)
    except (json.JSONDecodeError, PlatformSecretIsolationReceiptError):
        _invalid()
    trailing = text[end:]
    if (
        not isinstance(document, dict)
        or (trailing and not allow_trailing_whitespace)
        or (allow_trailing_whitespace and trailing.strip())
    ):
        _invalid()
    return document


def _exact_object(value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _invalid()
    return value


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_origin(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value != value.strip()
        or not value.isascii()
        or any(character in value for character in "\x00\r\n")
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    return bool(
        parsed.scheme == "https"
        and host
        and parsed.hostname == host
        and parsed.netloc == host
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _validate_release(release: Any) -> dict[str, Any]:
    normalized = _exact_object(release, _RELEASE_FIELDS)
    if (
        not isinstance(normalized["image_digest"], str)
        or not _SHA256.fullmatch(normalized["image_digest"])
        or normalized["image_digest"] == "sha256:" + "0" * 64
        or not isinstance(normalized["source_revision"], str)
        or not _HEX_40.fullmatch(normalized["source_revision"])
        or normalized["source_revision"] == "0" * 40
        or not isinstance(normalized["source_snapshot_sha256"], str)
        or not _SHA256.fullmatch(normalized["source_snapshot_sha256"])
        or normalized["source_snapshot_sha256"] == "sha256:" + "0" * 64
        or type(normalized["source_snapshot_file_count"]) is not int
        or normalized["source_snapshot_file_count"] < 1
        or not isinstance(normalized["upstream_revision"], str)
        or not _HEX_40.fullmatch(normalized["upstream_revision"])
        or normalized["upstream_revision"] == "0" * 40
        or not isinstance(normalized["route_acceptance_trust_keys_sha256"], str)
        or not _SHA256.fullmatch(
            normalized["route_acceptance_trust_keys_sha256"]
        )
        or normalized["route_acceptance_trust_keys_sha256"]
        == "sha256:" + "0" * 64
        or not isinstance(normalized["platform_image"], str)
        or not _PLATFORM_IMAGE.fullmatch(normalized["platform_image"])
        or normalized["platform_image"].endswith("@sha256:" + "0" * 64)
        or not isinstance(normalized["platform_source_revision"], str)
        or not _HEX_40.fullmatch(normalized["platform_source_revision"])
        or normalized["platform_source_revision"] == "0" * 40
        or not isinstance(normalized["platform_source_snapshot_sha256"], str)
        or not _SHA256.fullmatch(
            normalized["platform_source_snapshot_sha256"]
        )
        or normalized["platform_source_snapshot_sha256"]
        == "sha256:" + "0" * 64
        or not _canonical_origin(normalized["platform_origin"])
        or not _canonical_origin(normalized["relay_origin"])
        or not _canonical_origin(normalized["edge_origin"])
        or not isinstance(normalized["relay_contract_revision"], str)
        or re.fullmatch(
            r"[a-z][a-z0-9._-]{0,79}",
            normalized["relay_contract_revision"],
        )
        is None
    ):
        _invalid("Platform release identity is invalid")
    return normalized


def _validate_commitments(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int = 4096,
    expected_ids: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _invalid()
    result: list[dict[str, str]] = []
    for item in value:
        commitment = _exact_object(item, _COMMITMENT_FIELDS)
        identifier = commitment["id"]
        digest = commitment["sha256"]
        if (
            not isinstance(identifier, str)
            or not _COMMITMENT_ID.fullmatch(identifier)
            or not isinstance(digest, str)
            or not _BARE_SHA256.fullmatch(digest)
        ):
            _invalid()
        result.append({"id": identifier, "sha256": digest})
    identifiers = [item["id"] for item in result]
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        _invalid()
    if expected_ids is not None and identifiers != list(expected_ids):
        _invalid()
    return result


def _go_release(release: Mapping[str, Any]) -> dict[str, Any]:
    return {field: release[field] for field in _RELEASE_FIELD_ORDER}


def _go_commitments(value: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    return [
        {"id": item["id"], "sha256": item["sha256"]}
        for item in value
    ]


def _go_json(value: Mapping[str, Any]) -> bytes:
    # Contract strings are all validated ASCII, so encoding/json's HTML
    # escaping difference from Python cannot be reached.
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def _go_receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    return _go_json(
        {
            "schema_version": receipt["schema_version"],
            "kind": receipt["kind"],
            "run_id": receipt["run_id"],
            "consumer": receipt["consumer"],
            "release": _go_release(receipt["release"]),
            "files": _go_commitments(receipt["files"]),
            "semantics": _go_commitments(receipt["semantics"]),
        }
    )


def _go_commit_marker_bytes(marker: Mapping[str, Any]) -> bytes:
    return _go_json(
        {
            "schema_version": marker["schema_version"],
            "kind": marker["kind"],
            "run_id": marker["run_id"],
            "generation": marker["generation"],
            "root_proof_id": marker["root_proof_id"],
            "release": _go_release(marker["release"]),
            "receipts": _go_commitments(marker["receipts"]),
        }
    )


def parse_platform_secret_isolation_receipt(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > _MAXIMUM_RECEIPT_BYTES:
        _invalid()
    receipt = _exact_object(
        _strict_json_object(raw, allow_trailing_whitespace=True),
        _TOP_LEVEL_FIELDS,
    )
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != PLATFORM_SECRET_ISOLATION_RECEIPT_SCHEMA_VERSION
        or receipt["kind"] != PLATFORM_SECRET_ISOLATION_RECEIPT_KIND
        or not isinstance(receipt["run_id"], str)
        or not _RUN_ID.fullmatch(receipt["run_id"])
        or not isinstance(receipt["consumer"], str)
        or receipt["consumer"] not in _ALL_CONSUMERS
    ):
        _invalid()
    normalized = {
        "schema_version": receipt["schema_version"],
        "kind": receipt["kind"],
        "run_id": receipt["run_id"],
        "consumer": receipt["consumer"],
        "release": _validate_release(receipt["release"]),
        "files": _validate_commitments(receipt["files"], minimum=1, maximum=64),
        "semantics": _validate_commitments(receipt["semantics"]),
    }
    if not hmac.compare_digest(raw, _go_receipt_bytes(normalized)):
        _invalid()
    return normalized


def parse_platform_secret_isolation_commit_marker(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > _MAXIMUM_RECEIPT_BYTES:
        _invalid("Platform secret isolation commit marker is invalid")
    marker = _exact_object(
        _strict_json_object(raw, allow_trailing_whitespace=False),
        _COMMIT_FIELDS,
    )
    if (
        type(marker["schema_version"]) is not int
        or marker["schema_version"] != PLATFORM_SECRET_ISOLATION_COMMIT_SCHEMA_VERSION
        or marker["kind"] != PLATFORM_SECRET_ISOLATION_COMMIT_KIND
        or not isinstance(marker["run_id"], str)
        or not _RUN_ID.fullmatch(marker["run_id"])
        or marker["generation"] != PLATFORM_SECRET_ISOLATION_REQUIRED_GENERATION
        or not isinstance(marker["root_proof_id"], str)
        or not _RUN_ID.fullmatch(marker["root_proof_id"])
    ):
        _invalid("Platform secret isolation commit marker is invalid")
    normalized = {
        "schema_version": marker["schema_version"],
        "kind": marker["kind"],
        "run_id": marker["run_id"],
        "generation": marker["generation"],
        "root_proof_id": marker["root_proof_id"],
        "release": _validate_release(marker["release"]),
        "receipts": _validate_commitments(
            marker["receipts"],
            minimum=len(_ALL_CONSUMERS),
            maximum=len(_ALL_CONSUMERS),
            expected_ids=_ALL_CONSUMERS,
        ),
    }
    if not hmac.compare_digest(raw, _go_commit_marker_bytes(normalized)):
        _invalid("Platform secret isolation commit marker is invalid")
    return normalized


def _read_fixed_release_identity_file() -> bytes:
    path = PLATFORM_RELEASE_IDENTITY_FILE
    try:
        before = os.lstat(path)
    except OSError:
        _invalid("Platform image release identity is unavailable")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) != 0o444
        or before.st_size < 1
        or before.st_size > _MAXIMUM_RELEASE_IDENTITY_BYTES
    ):
        _invalid("Platform image release identity protection is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _invalid("Platform image release identity is unavailable")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o444
            or opened.st_size != before.st_size
        ):
            _invalid("Platform image release identity protection is invalid")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                _invalid("Platform image release identity is unavailable")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.lstat(path)
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            _invalid("Platform image release identity changed during validation")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _embedded_platform_identity() -> dict[str, str]:
    document = _exact_object(
        _strict_json_object(
            _read_fixed_release_identity_file(),
            allow_trailing_whitespace=False,
        ),
        _PLATFORM_IDENTITY_FIELDS,
    )
    revision = document["platform_source_revision"]
    snapshot = document["platform_source_snapshot_sha256"]
    if (
        not isinstance(revision, str)
        or not _HEX_40.fullmatch(revision)
        or revision == "0" * 40
        or not isinstance(snapshot, str)
        or not _SHA256.fullmatch(snapshot)
        or snapshot == "sha256:" + "0" * 64
    ):
        _invalid("Platform image release identity is invalid")
    return {
        "platform_source_revision": revision,
        "platform_source_snapshot_sha256": snapshot,
    }


def expected_platform_release_identity() -> dict[str, Any]:
    release: dict[str, Any] = {}
    for field, environment in _RELEASE_ENVIRONMENT.items():
        raw = os.environ.get(environment)
        if raw is None or raw != raw.strip() or raw != raw.lower():
            _invalid("Platform release environment is invalid")
        if field == "source_snapshot_file_count":
            if re.fullmatch(r"[1-9][0-9]*", raw) is None:
                _invalid("Platform release environment is invalid")
            try:
                release[field] = int(raw, 10)
            except ValueError:
                _invalid("Platform release environment is invalid")
        else:
            release[field] = raw
    release = _validate_release(release)
    embedded = _embedded_platform_identity()
    if not hmac.compare_digest(
        _canonical_json(
            {
                "platform_source_revision": release["platform_source_revision"],
                "platform_source_snapshot_sha256": release[
                    "platform_source_snapshot_sha256"
                ],
            }
        ),
        _canonical_json(embedded),
    ):
        _invalid("Platform image release identity does not match the environment")
    return release


def _receipt_mount_is_read_only(descriptor: int) -> bool:
    if not sys.platform.startswith("linux"):
        return True
    return bool(
        os.fstatvfs(descriptor).f_flag & getattr(os, "ST_RDONLY", 1)
    )


def _read_protected_isolation_file(environment: str, label: str) -> bytes:
    path = os.environ.get(environment, "")
    if (
        not path
        or path != path.strip()
        or "\x00" in path
        or "\r" in path
        or "\n" in path
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
        or os.path.realpath(path) != path
    ):
        _invalid(f"Platform secret isolation {label} path is invalid")
    try:
        before = os.lstat(path)
    except OSError:
        _invalid(f"Platform secret isolation {label} is unavailable")
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else before.st_uid
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != effective_uid
        or stat.S_IMODE(before.st_mode) != 0o400
        or before.st_size < 1
        or before.st_size > _MAXIMUM_RECEIPT_BYTES
    ):
        _invalid(f"Platform secret isolation {label} protection is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _invalid(f"Platform secret isolation {label} is unavailable")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_uid != effective_uid
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_size != before.st_size
            or not _receipt_mount_is_read_only(descriptor)
        ):
            _invalid(f"Platform secret isolation {label} protection is invalid")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _invalid(f"Platform secret isolation {label} is unavailable")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.lstat(path)
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            _invalid(f"Platform secret isolation {label} changed during validation")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_protected_platform_secret_isolation_receipt() -> bytes:
    return _read_protected_isolation_file(
        PLATFORM_SECRET_ISOLATION_RECEIPT_ENV,
        "receipt",
    )


def read_protected_platform_secret_isolation_commit_marker() -> bytes:
    return _read_protected_isolation_file(
        PLATFORM_SECRET_ISOLATION_COMMIT_ENV,
        "commit marker",
    )


def verify_platform_secret_isolation_receipt_sources(
    *,
    consumer: str,
    files: Mapping[str, bytes],
    semantics: Sequence[Mapping[str, str]],
) -> PlatformSecretIsolationContext:
    if (
        not files
        or len(files) > 64
        or any(
            not isinstance(identifier, str)
            or not _COMMITMENT_ID.fullmatch(identifier)
            or not isinstance(raw, bytes)
            or not raw
            for identifier, raw in files.items()
        )
    ):
        _invalid("Platform secret isolation sources are invalid")
    expected_semantics = _validate_commitments(
        [dict(item) for item in semantics]
    )
    release = expected_platform_release_identity()
    receipt_raw = read_protected_platform_secret_isolation_receipt()
    actual = parse_platform_secret_isolation_receipt(receipt_raw)
    marker = parse_platform_secret_isolation_commit_marker(
        read_protected_platform_secret_isolation_commit_marker()
    )
    expected = {
        "schema_version": PLATFORM_SECRET_ISOLATION_RECEIPT_SCHEMA_VERSION,
        "kind": PLATFORM_SECRET_ISOLATION_RECEIPT_KIND,
        "run_id": marker["run_id"],
        "consumer": consumer,
        "release": release,
        "files": [
            {"id": identifier, "sha256": hashlib.sha256(files[identifier]).hexdigest()}
            for identifier in sorted(files)
        ],
        "semantics": expected_semantics,
    }
    committed_digest = next(
        (
            item["sha256"]
            for item in marker["receipts"]
            if item["id"] == consumer
        ),
        "",
    )
    if (
        actual["consumer"] != consumer
        or marker["release"] != release
        or actual["release"] != release
        or actual["run_id"] != marker["run_id"]
        or not hmac.compare_digest(
            committed_digest,
            hashlib.sha256(receipt_raw).hexdigest(),
        )
        or not hmac.compare_digest(
        _canonical_json(actual), _canonical_json(expected)
        )
    ):
        _invalid("Platform secret isolation receipt does not match mounted sources")
    return PlatformSecretIsolationContext(
        run_id=marker["run_id"],
        generation=marker["generation"],
        root_proof_id=marker["root_proof_id"],
        platform_image=str(release["platform_image"]),
        platform_source_revision=str(release["platform_source_revision"]),
        platform_source_snapshot_sha256=str(
            release["platform_source_snapshot_sha256"]
        ),
    )


def verify_platform_secret_isolation_receipt(
    *,
    bundle_raw: bytes,
    consumer: str,
    file_id: str,
    semantics: Sequence[Mapping[str, str]],
) -> PlatformSecretIsolationContext:
    return verify_platform_secret_isolation_receipt_sources(
        consumer=consumer,
        files={file_id: bundle_raw},
        semantics=semantics,
    )
