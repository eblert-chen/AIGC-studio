from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json

import pytest

from platform_api.models import DownloadCompletionSource
from platform_api.schemas import (
    EdgeGatewayDownloadCompletionRequest,
    ObsAccessLogDownloadCompletionRequest,
)
from platform_api.services.download_completion_events import (
    DownloadCompletionEventPayloadError,
    DownloadCompletionEventVerificationError,
    DownloadCompletionEventVerifier,
)

NOW = 1_800_000_000
EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EDGE_SECRET = "edge-gateway-download-secret-32-bytes-minimum"
OBS_SECRET = "obs-access-log-download-secret-32-bytes-minimum"


def _edge_payload() -> dict[str, object]:
    return {
        "download_record_id": "11111111-1111-4111-8111-111111111111",
        "company_id": "22222222-2222-4222-8222-222222222222",
        "task_id": "33333333-3333-4333-8333-333333333333",
        "asset_id": "artifact-video-0001",
        "external_event_id": "edge-download-event-0001",
        "bytes_sent": 4096,
        "completed_at": "2027-01-15T08:00:00+00:00",
        "artifact_sha256": "a" * 64,
        "expected_size_bytes": 4096,
        "http_status": 200,
        "transfer_scope": "full_body",
        "gateway_request_id": "gateway-request-0001",
        "gateway_transfer_reference": "gateway-transfer-0001",
    }


def _obs_payload() -> dict[str, object]:
    return {
        "download_record_id": "11111111-1111-4111-8111-111111111111",
        "company_id": "22222222-2222-4222-8222-222222222222",
        "task_id": "33333333-3333-4333-8333-333333333333",
        "asset_id": "artifact-video-0001",
        "external_event_id": "obs-download-event-0001",
        "bytes_sent": 4096,
        "completed_at": "2027-01-15T08:00:00+00:00",
        "artifact_sha256": "a" * 64,
        "expected_size_bytes": 4096,
        "http_status": 200,
        "transfer_scope": "full_body",
        "obs_bucket": "private-artifact-bucket",
        "obs_object_key": "companies/2222/tasks/3333/artifact-video-0001.mp4",
        "obs_version_id": "obs-version-0001",
        "obs_request_id": "obs-request-0001",
    }


def _raw(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(
    raw_body: bytes,
    *,
    source: DownloadCompletionSource,
    secret: str,
    timestamp: str = str(NOW),
    event_id: str = EVENT_ID,
) -> str:
    signing_input = (
        b"download-completion.v1\n"
        + source.value.encode("ascii")
        + b"\n"
        + timestamp.encode("ascii")
        + b"\n"
        + event_id.encode("ascii")
        + b"\n"
        + raw_body
    )
    return (
        "v1="
        + hmac.new(
            secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).hexdigest()
    )


@pytest.fixture
def verifier() -> DownloadCompletionEventVerifier:
    return DownloadCompletionEventVerifier(
        edge_gateway_signing_secret=EDGE_SECRET,
        obs_access_log_signing_secret=OBS_SECRET,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("payload_factory", "source", "secret", "schema"),
    [
        (
            _edge_payload,
            DownloadCompletionSource.EDGE_GATEWAY,
            EDGE_SECRET,
            EdgeGatewayDownloadCompletionRequest,
        ),
        (
            _obs_payload,
            DownloadCompletionSource.OBS_ACCESS_LOG,
            OBS_SECRET,
            ObsAccessLogDownloadCompletionRequest,
        ),
    ],
)
def test_verifies_each_fixed_source_with_its_own_secret(
    verifier,
    payload_factory,
    source,
    secret,
    schema,
):
    raw_body = _raw(payload_factory())
    payload, evidence = verifier.verify(
        raw_body,
        source=source,
        event_id=EVENT_ID,
        timestamp=str(NOW),
        signature=_signature(raw_body, source=source, secret=secret),
    )

    assert isinstance(payload, schema)
    assert evidence.event_id == EVENT_ID
    assert evidence.event_timestamp == datetime.fromtimestamp(
        NOW,
        tz=timezone.utc,
    )
    assert evidence.payload_sha256 == hashlib.sha256(raw_body).hexdigest()


@pytest.mark.parametrize(
    ("payload_factory", "source", "wrong_secret"),
    [
        (
            _edge_payload,
            DownloadCompletionSource.EDGE_GATEWAY,
            OBS_SECRET,
        ),
        (
            _obs_payload,
            DownloadCompletionSource.OBS_ACCESS_LOG,
            EDGE_SECRET,
        ),
    ],
)
def test_cross_source_secret_is_rejected(
    verifier,
    payload_factory,
    source,
    wrong_secret,
):
    raw_body = _raw(payload_factory())
    with pytest.raises(DownloadCompletionEventVerificationError) as error:
        verifier.verify(
            raw_body,
            source=source,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                raw_body,
                source=source,
                secret=wrong_secret,
            ),
        )
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    ("event_id", "timestamp", "signature"),
    [
        (None, None, None),
        (EVENT_ID, None, None),
        (EVENT_ID, str(NOW), None),
        (EVENT_ID.upper(), str(NOW), "v1=" + ("0" * 64)),
        ("{aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa}", str(NOW), "v1=" + ("0" * 64)),
        (EVENT_ID, "01800000000", "v1=" + ("0" * 64)),
        (EVENT_ID, str(NOW), "V1=" + ("0" * 64)),
        (EVENT_ID, str(NOW), "v1=" + ("A" * 64)),
        (EVENT_ID, str(NOW), "v1=short"),
    ],
)
def test_missing_partial_or_noncanonical_headers_are_rejected(
    verifier,
    event_id,
    timestamp,
    signature,
):
    with pytest.raises(DownloadCompletionEventVerificationError) as error:
        verifier.verify(
            _raw(_edge_payload()),
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=event_id,
            timestamp=timestamp,
            signature=signature,
        )
    assert error.value.status_code == 401


@pytest.mark.parametrize("timestamp", [NOW - 301, NOW + 301])
def test_stale_and_future_signatures_outside_the_window_are_rejected(
    verifier,
    timestamp,
):
    raw_body = _raw(_edge_payload())
    timestamp_text = str(timestamp)
    with pytest.raises(DownloadCompletionEventVerificationError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=timestamp_text,
            signature=_signature(
                raw_body,
                source=DownloadCompletionSource.EDGE_GATEWAY,
                secret=EDGE_SECRET,
                timestamp=timestamp_text,
            ),
        )
    assert error.value.status_code == 401


def test_exact_window_boundary_is_accepted(verifier):
    raw_body = _raw(_edge_payload())
    timestamp = str(NOW - 300)
    payload, _ = verifier.verify(
        raw_body,
        source=DownloadCompletionSource.EDGE_GATEWAY,
        event_id=EVENT_ID,
        timestamp=timestamp,
        signature=_signature(
            raw_body,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            secret=EDGE_SECRET,
            timestamp=timestamp,
        ),
    )
    assert isinstance(payload, EdgeGatewayDownloadCompletionRequest)


def test_body_tampering_and_reserialization_are_rejected(verifier):
    raw_body = _raw(_edge_payload())
    signature = _signature(
        raw_body,
        source=DownloadCompletionSource.EDGE_GATEWAY,
        secret=EDGE_SECRET,
    )
    tampered = raw_body.replace(b'"bytes_sent":4096', b'"bytes_sent":4095')
    pretty = json.dumps(_edge_payload(), indent=2).encode("utf-8")

    for changed_body in (tampered, pretty):
        with pytest.raises(DownloadCompletionEventVerificationError) as error:
            verifier.verify(
                changed_body,
                source=DownloadCompletionSource.EDGE_GATEWAY,
                event_id=EVENT_ID,
                timestamp=str(NOW),
                signature=signature,
            )
        assert error.value.status_code == 401


def test_duplicate_json_keys_are_rejected(verifier):
    raw_body = _raw(_edge_payload())
    duplicated = raw_body.replace(
        b'"bytes_sent":4096',
        b'"bytes_sent":4096,"bytes_sent":4096',
    )
    with pytest.raises(DownloadCompletionEventPayloadError) as error:
        verifier.verify(
            duplicated,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                duplicated,
                source=DownloadCompletionSource.EDGE_GATEWAY,
                secret=EDGE_SECRET,
            ),
        )
    assert error.value.status_code == 422


def test_invalid_signature_is_rejected_before_malformed_json_is_parsed(verifier):
    with pytest.raises(DownloadCompletionEventVerificationError) as error:
        verifier.verify(
            b'{"malformed":',
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature="v1=" + ("0" * 64),
        )
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "raw_body",
    [
        b"not-json",
        b"[]",
    ],
)
def test_malformed_payload_is_rejected(
    verifier,
    raw_body,
):
    with pytest.raises(DownloadCompletionEventPayloadError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                raw_body,
                source=DownloadCompletionSource.EDGE_GATEWAY,
                secret=EDGE_SECRET,
            ),
        )
    assert error.value.status_code == 422


def test_request_body_cannot_select_its_source(verifier):
    payload = _edge_payload()
    payload["source"] = "edge_gateway"
    raw_body = _raw(payload)
    with pytest.raises(DownloadCompletionEventPayloadError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                raw_body,
                source=DownloadCompletionSource.EDGE_GATEWAY,
                secret=EDGE_SECRET,
            ),
        )
    assert error.value.status_code == 422


def test_only_two_server_fixed_sources_are_authorized(verifier):
    raw_body = _raw(_edge_payload())
    with pytest.raises(DownloadCompletionEventVerificationError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.PLATFORM_PROXY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                raw_body,
                source=DownloadCompletionSource.PLATFORM_PROXY,
                secret=EDGE_SECRET,
            ),
        )
    assert error.value.status_code == 401


def test_signature_cannot_be_reused_at_the_other_source_endpoint(verifier):
    raw_body = _raw(_edge_payload())
    edge_signature = _signature(
        raw_body,
        source=DownloadCompletionSource.EDGE_GATEWAY,
        secret=EDGE_SECRET,
    )
    with pytest.raises(DownloadCompletionEventVerificationError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.OBS_ACCESS_LOG,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=edge_signature,
        )
    assert error.value.status_code == 401


def test_schema_validation_occurs_only_after_authentication(verifier):
    invalid_payload = _edge_payload()
    invalid_payload.pop("gateway_request_id")
    raw_body = _raw(invalid_payload)

    with pytest.raises(DownloadCompletionEventVerificationError):
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature="v1=" + ("0" * 64),
        )

    with pytest.raises(DownloadCompletionEventPayloadError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                raw_body,
                source=DownloadCompletionSource.EDGE_GATEWAY,
                secret=EDGE_SECRET,
            ),
        )
    assert error.value.status_code == 422


def test_obs_requires_version_or_request_identity_after_authentication(
    verifier,
):
    invalid_payload = _obs_payload()
    invalid_payload.pop("obs_version_id")
    invalid_payload.pop("obs_request_id")
    raw_body = _raw(invalid_payload)
    with pytest.raises(DownloadCompletionEventPayloadError) as error:
        verifier.verify(
            raw_body,
            source=DownloadCompletionSource.OBS_ACCESS_LOG,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=_signature(
                raw_body,
                source=DownloadCompletionSource.OBS_ACCESS_LOG,
                secret=OBS_SECRET,
            ),
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "edge_gateway_signing_secret": "",
            "obs_access_log_signing_secret": OBS_SECRET,
        },
        {
            "edge_gateway_signing_secret": EDGE_SECRET,
            "obs_access_log_signing_secret": "",
        },
        {
            "edge_gateway_signing_secret": EDGE_SECRET,
            "obs_access_log_signing_secret": EDGE_SECRET,
        },
    ],
)
def test_verifier_requires_two_nonempty_independent_secrets(kwargs):
    with pytest.raises(ValueError):
        DownloadCompletionEventVerifier(**kwargs)


def test_verifier_rejects_unreasonably_short_replay_window():
    with pytest.raises(ValueError, match="at least 30 seconds"):
        DownloadCompletionEventVerifier(
            edge_gateway_signing_secret=EDGE_SECRET,
            obs_access_log_signing_secret=OBS_SECRET,
            max_age_seconds=29,
        )
