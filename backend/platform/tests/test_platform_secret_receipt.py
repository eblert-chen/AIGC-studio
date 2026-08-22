from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

import pytest

from platform_api import (
    platform_database_release_proof,
    platform_secret_receipt,
    process_secrets,
)
from platform_api.platform_secret_receipt import (
    PlatformSecretIsolationContext,
    PlatformSecretIsolationReceiptError,
    expected_platform_release_identity,
    parse_platform_secret_isolation_receipt,
    verify_platform_secret_isolation_receipt_sources,
)


PLATFORM_DATABASE_CA_PATH = "/run/secrets/platform-database-ca.pem"
PLATFORM_DATABASE_CA_RAW = b"synthetic-platform-database-ca-commitment"


@pytest.fixture(autouse=True)
def _platform_database_ca_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        process_secrets.PLATFORM_DATABASE_CA_FILE_ENV,
        PLATFORM_DATABASE_CA_PATH,
    )


def _secret(label: str) -> str:
    return f"v1-{label}-{hashlib.sha256(label.encode()).hexdigest()}-X9!"


def _database_password(label: str) -> str:
    return hashlib.sha512(("platform-db:" + label).encode()).hexdigest()


def _migration_raw() -> bytes:
    document = {
        "kind": "platform_process_runtime_secrets",
        "schema_version": 1,
        "process_role": "migration",
        "secrets": {
            "database_url": (
                "postgresql+psycopg://platform_migration:"
                + _database_password("migration-database")
                + "@postgres.example.invalid:5432/ai_video?"
                + urlencode(
                    (
                        ("sslmode", "verify-full"),
                        ("sslrootcert", PLATFORM_DATABASE_CA_PATH),
                    )
                )
            )
        },
    }
    return json.dumps(document, separators=(",", ":")).encode()


def _release() -> dict[str, object]:
    return {
        "image_digest": "sha256:" + "1" * 64,
        "source_revision": "2" * 40,
        "source_snapshot_sha256": "sha256:" + "3" * 64,
        "source_snapshot_file_count": 271,
        "upstream_revision": "4" * 40,
        "route_acceptance_trust_keys_sha256": "sha256:" + "5" * 64,
        "platform_image": "registry.example.invalid/ai-video/platform@sha256:"
        + "6" * 64,
        "platform_source_revision": "7" * 40,
        "platform_source_snapshot_sha256": "sha256:" + "8" * 64,
        "platform_origin": "https://platform.example.invalid",
        "relay_origin": "https://relay.example.invalid",
        "edge_origin": "https://downloads.example.invalid",
        "relay_contract_revision": "generations.v1",
    }


def _migration_receipt(raw: bytes | None = None) -> dict[str, object]:
    bundle = raw or _migration_raw()
    normalized = process_secrets.parse_platform_process_secret_document(
        bundle, "migration"
    )
    return {
        "schema_version": 2,
        "kind": "relay_secret_isolation_commitment",
        "run_id": "a" * 64,
        "consumer": "platform-migration",
        "release": _release(),
        "files": [
            {
                "id": "platform_database_ca",
                "sha256": hashlib.sha256(PLATFORM_DATABASE_CA_RAW).hexdigest(),
            },
            {
                "id": "platform_migration_runtime",
                "sha256": hashlib.sha256(bundle).hexdigest(),
            }
        ],
        "semantics": list(
            process_secrets.platform_process_secret_semantic_commitments(
                normalized, "migration"
            )
        ),
    }


def _receipt_raw(receipt: dict[str, object]) -> bytes:
    return platform_secret_receipt._go_receipt_bytes(receipt)


def _commit_marker(receipt_raw: bytes) -> dict[str, object]:
    receipts = [
        {"id": consumer, "sha256": "9" * 64}
        for consumer in platform_secret_receipt._ALL_CONSUMERS
    ]
    next(
        item for item in receipts if item["id"] == "platform-migration"
    )["sha256"] = hashlib.sha256(receipt_raw).hexdigest()
    return {
        "schema_version": 2,
        "kind": "relay_secret_isolation_commit",
        "run_id": "a" * 64,
        "generation": "root-proof-present",
        "root_proof_id": "b" * 64,
        "release": _release(),
        "receipts": receipts,
    }


def _commit_raw(receipt_raw: bytes) -> bytes:
    return platform_secret_receipt._go_commit_marker_bytes(
        _commit_marker(receipt_raw)
    )


def _set_release_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _release()
    for field, environment in platform_secret_receipt._RELEASE_ENVIRONMENT.items():
        monkeypatch.setenv(environment, str(release[field]))


def test_receipt_parser_accepts_the_closed_go_json_line_contract() -> None:
    receipt = _migration_receipt()
    assert parse_platform_secret_isolation_receipt(_receipt_raw(receipt)) == receipt


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.replace(
            b'"schema_version":2',
            b'"schema_version":2,"schema_version":2',
            1,
        ),
        lambda raw: raw.replace(b'"consumer":', b'"unknown":1,"consumer":', 1),
        lambda raw: raw + b"{}",
        lambda raw: raw.replace(b'"schema_version":2', b'"schema_version":true'),
        lambda raw: raw.replace(
            b'"image_digest":', b'"release_unknown":1,"image_digest":', 1
        ),
    ],
)
def test_receipt_parser_rejects_duplicate_unknown_trailing_and_wrong_types(
    mutate,
) -> None:
    with pytest.raises(PlatformSecretIsolationReceiptError):
        parse_platform_secret_isolation_receipt(
            mutate(_receipt_raw(_migration_receipt()))
        )


def test_receipt_parser_rejects_duplicate_or_unsorted_commitment_ids() -> None:
    receipt = _migration_receipt()
    receipt["semantics"] = [
        receipt["semantics"][0],
        dict(receipt["semantics"][0]),
    ]
    with pytest.raises(PlatformSecretIsolationReceiptError):
        parse_platform_secret_isolation_receipt(_receipt_raw(receipt))


def test_commit_marker_is_closed_canonical_and_lists_all_consumers() -> None:
    receipt_raw = _receipt_raw(_migration_receipt())
    raw = _commit_raw(receipt_raw)
    marker = platform_secret_receipt.parse_platform_secret_isolation_commit_marker(
        raw
    )
    assert [item["id"] for item in marker["receipts"]] == list(
        platform_secret_receipt._ALL_CONSUMERS
    )
    assert marker["generation"] == "root-proof-present"
    assert marker["root_proof_id"] == "b" * 64
    with pytest.raises(PlatformSecretIsolationReceiptError):
        platform_secret_receipt.parse_platform_secret_isolation_commit_marker(
            raw + b"\n"
        )

    receipt = _migration_receipt()
    receipt["semantics"] = [
        {"id": "z.synthetic", "sha256": "9" * 64},
        receipt["semantics"][0],
    ]
    with pytest.raises(PlatformSecretIsolationReceiptError):
        parse_platform_secret_isolation_receipt(_receipt_raw(receipt))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda marker: marker.update(
            generation="pre-root",
            root_proof_id="",
        ),
        lambda marker: marker.update(root_proof_id=""),
        lambda marker: marker.update(root_proof_id="A" * 64),
        lambda marker: marker.update(schema_version=1),
    ),
)
def test_every_platform_consumer_rejects_pre_root_or_invalid_proof_generation(
    mutate,
) -> None:
    receipt_raw = _receipt_raw(_migration_receipt())
    marker = _commit_marker(receipt_raw)
    mutate(marker)
    with pytest.raises(PlatformSecretIsolationReceiptError):
        platform_secret_receipt.parse_platform_secret_isolation_commit_marker(
            platform_secret_receipt._go_commit_marker_bytes(marker)
        )


def test_marker_v2_bytes_match_the_go_cross_language_golden() -> None:
    release = {
        "image_digest": "sha256:" + "4" * 64,
        "source_revision": "1" * 40,
        "source_snapshot_sha256": "sha256:" + "2" * 64,
        "source_snapshot_file_count": 37,
        "upstream_revision": "0ab02020603d22e5613bc4cf46bfab06f8567769",
        "route_acceptance_trust_keys_sha256": "sha256:" + "3" * 64,
        "platform_image": (
            "registry.example.test/ai-video/platform@sha256:" + "5" * 64
        ),
        "platform_source_revision": "6" * 40,
        "platform_source_snapshot_sha256": "sha256:" + "7" * 64,
        "platform_origin": "https://platform.example.test",
        "relay_origin": "https://relay.example.test",
        "edge_origin": "https://downloads.example.test",
        "relay_contract_revision": "generations.v1",
    }
    marker = {
        "schema_version": 2,
        "kind": "relay_secret_isolation_commit",
        "run_id": "a" * 64,
        "generation": "root-proof-present",
        "root_proof_id": "b" * 64,
        "release": release,
        "receipts": [
            {
                "id": consumer,
                "sha256": hashlib.sha256(
                    ("receipt:" + consumer).encode("ascii")
                ).hexdigest(),
            }
            for consumer in platform_secret_receipt._ALL_CONSUMERS
        ],
    }
    raw = platform_secret_receipt._go_commit_marker_bytes(marker)
    assert hashlib.sha256(raw).hexdigest() == (
        "80df3044f8b70c36b106ebf3b32c50159e25bc8d7f97e682dcf82d1918729c17"
    )
    assert (
        platform_secret_receipt.parse_platform_secret_isolation_commit_marker(
            raw
        )
        == marker
    )


def test_embedded_image_identity_must_exactly_match_release_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_release_environment(monkeypatch)
    embedded = {
        "platform_source_revision": _release()["platform_source_revision"],
        "platform_source_snapshot_sha256": _release()[
            "platform_source_snapshot_sha256"
        ],
    }
    monkeypatch.setattr(
        platform_secret_receipt,
        "_read_fixed_release_identity_file",
        lambda: json.dumps(embedded, separators=(",", ":")).encode(),
    )
    assert expected_platform_release_identity() == _release()

    embedded["platform_source_revision"] = "9" * 40
    with pytest.raises(
        PlatformSecretIsolationReceiptError,
        match="does not match",
    ):
        expected_platform_release_identity()


def test_platform_image_release_rejects_an_all_zero_manifest_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release()
    release["platform_image"] = (
        "registry.example.invalid/ai-video/platform@sha256:" + "0" * 64
    )
    for field, environment in platform_secret_receipt._RELEASE_ENVIRONMENT.items():
        monkeypatch.setenv(environment, str(release[field]))
    monkeypatch.setattr(
        platform_secret_receipt,
        "_read_fixed_release_identity_file",
        lambda: json.dumps(
            {
                "platform_source_revision": release["platform_source_revision"],
                "platform_source_snapshot_sha256": release[
                    "platform_source_snapshot_sha256"
                ],
            },
            separators=(",", ":"),
        ).encode(),
    )
    with pytest.raises(PlatformSecretIsolationReceiptError):
        expected_platform_release_identity()


@pytest.mark.parametrize("raw_count", ["001", "+1", " 1", "1 ", "0", "-1"])
def test_release_file_count_requires_canonical_positive_decimal(
    monkeypatch: pytest.MonkeyPatch,
    raw_count: str,
) -> None:
    _set_release_environment(monkeypatch)
    monkeypatch.setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", raw_count)
    with pytest.raises(PlatformSecretIsolationReceiptError):
        expected_platform_release_identity()


def test_verifier_binds_same_raw_bytes_semantics_consumer_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _migration_raw()
    normalized = process_secrets.parse_platform_process_secret_document(
        raw, "migration"
    )
    semantics = process_secrets.platform_process_secret_semantic_commitments(
        normalized, "migration"
    )
    receipt = _migration_receipt(raw)
    monkeypatch.setattr(
        platform_secret_receipt,
        "expected_platform_release_identity",
        _release,
    )
    monkeypatch.setattr(
        platform_secret_receipt,
        "read_protected_platform_secret_isolation_receipt",
        lambda: _receipt_raw(receipt),
    )
    monkeypatch.setattr(
        platform_secret_receipt,
        "read_protected_platform_secret_isolation_commit_marker",
        lambda: _commit_raw(_receipt_raw(receipt)),
    )
    verify_platform_secret_isolation_receipt_sources(
        consumer="platform-migration",
        files={
            "platform_migration_runtime": raw,
            "platform_database_ca": PLATFORM_DATABASE_CA_RAW,
        },
        semantics=semantics,
    )

    receipt["files"][1]["sha256"] = "9" * 64
    monkeypatch.setattr(
        platform_secret_receipt,
        "read_protected_platform_secret_isolation_commit_marker",
        lambda: _commit_raw(_receipt_raw(receipt)),
    )
    with pytest.raises(
        PlatformSecretIsolationReceiptError,
        match="does not match mounted sources",
    ):
        verify_platform_secret_isolation_receipt_sources(
            consumer="platform-migration",
            files={
                "platform_migration_runtime": raw,
                "platform_database_ca": PLATFORM_DATABASE_CA_RAW,
            },
            semantics=semantics,
        )


def test_protected_loader_reads_bundle_once_then_verifies_that_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _migration_raw()
    reads: list[bytes] = []
    verified: list[dict[str, object]] = []
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "migration")
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    def read_once() -> bytes:
        reads.append(raw)
        return raw

    def verify(**values) -> PlatformSecretIsolationContext:
        verified.append(values)
        return PlatformSecretIsolationContext(
            run_id="a" * 64,
            generation="root-proof-present",
            root_proof_id="b" * 64,
            platform_image=str(_release()["platform_image"]),
            platform_source_revision="7" * 40,
            platform_source_snapshot_sha256="sha256:" + "8" * 64,
        )

    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_process_secret_file",
        read_once,
    )
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_database_ca_file",
        lambda: PLATFORM_DATABASE_CA_RAW,
    )
    monkeypatch.setattr(
        platform_secret_receipt,
        "verify_platform_secret_isolation_receipt_sources",
        verify,
    )
    monkeypatch.setattr(
        process_secrets,
        "materialize_verified_platform_database_ca",
        lambda _: "/run/platform-database-ca-snapshot/committed.pem",
    )
    installed: list[dict[str, object]] = []
    monkeypatch.setattr(
        platform_database_release_proof,
        "load_and_install_platform_database_release_proof",
        lambda **values: installed.append(values),
    )
    result = process_secrets.load_platform_process_secret_settings()
    assert result["process_role"] == "migration"
    assert reads == [raw]
    assert verified[0]["consumer"] == "platform-migration"
    assert verified[0]["files"] == {
        "platform_migration_runtime": raw,
        "platform_database_ca": PLATFORM_DATABASE_CA_RAW,
    }
    assert installed[0]["isolation"].run_id == "a" * 64
    assert installed[0]["database_endpoint_sha256"] == next(
        item["sha256"]
        for item in verified[0]["semantics"]
        if item["id"].endswith(".database.endpoint")
    )


def test_receipt_failure_never_echoes_bundle_canary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _migration_raw()
    canary = _database_password("migration-database")
    normalized = process_secrets.parse_platform_process_secret_document(
        raw, "migration"
    )
    monkeypatch.setattr(
        platform_secret_receipt,
        "expected_platform_release_identity",
        _release,
    )
    receipt = _migration_receipt(raw)
    receipt["consumer"] = "platform-api"
    monkeypatch.setattr(
        platform_secret_receipt,
        "read_protected_platform_secret_isolation_receipt",
        lambda: _receipt_raw(receipt),
    )
    monkeypatch.setattr(
        platform_secret_receipt,
        "read_protected_platform_secret_isolation_commit_marker",
        lambda: _commit_raw(_receipt_raw(receipt)),
    )
    with pytest.raises(PlatformSecretIsolationReceiptError) as captured:
        verify_platform_secret_isolation_receipt_sources(
            consumer="platform-migration",
            files={
                "platform_migration_runtime": raw,
                "platform_database_ca": PLATFORM_DATABASE_CA_RAW,
            },
            semantics=process_secrets.platform_process_secret_semantic_commitments(
                normalized, "migration"
            ),
        )
    assert canary not in str(captured.value)
