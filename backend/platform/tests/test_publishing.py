from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from platform_api.config import Settings
from platform_api.models import (
    AuditLog,
    CompanyResourceGrant,
    GenerationTask,
    MemberPermissionOverride,
    ModelDefinition,
    PublicationJob,
    PublicationJobStatus,
    PermissionEffect,
    ResourceDefinition,
    ResourceKind,
    TaskArtifact,
    TaskStatus,
)
from platform_api.services.publishing import AUTO_PUBLISH_RESOURCE_KEY

from .conftest import bootstrap
from .test_production_config import production_settings


def _enable_auto_publish(app, company_id: str) -> None:
    with app.state.session_factory.begin() as session:
        resource = session.scalar(
            select(ResourceDefinition).where(
                ResourceDefinition.key == AUTO_PUBLISH_RESOURCE_KEY
            )
        )
        if resource is None:
            resource = ResourceDefinition(
                key=AUTO_PUBLISH_RESOURCE_KEY,
                kind=ResourceKind.FEATURE,
                display_name="Automatic publishing",
                description="Publishing test entitlement",
                active=True,
            )
            session.add(resource)
            session.flush()
        grant = session.scalar(
            select(CompanyResourceGrant).where(
                CompanyResourceGrant.company_id == company_id,
                CompanyResourceGrant.resource_id == resource.id,
            )
        )
        if grant is None:
            grant = CompanyResourceGrant(
                company_id=company_id,
                resource_id=resource.id,
                config_override={},
            )
            session.add(grant)
        grant.enabled = True


def _disable_auto_publish(app, company_id: str) -> None:
    with app.state.session_factory.begin() as session:
        grant = session.scalar(
            select(CompanyResourceGrant)
            .join(
                ResourceDefinition,
                ResourceDefinition.id == CompanyResourceGrant.resource_id,
            )
            .where(
                CompanyResourceGrant.company_id == company_id,
                ResourceDefinition.key == AUTO_PUBLISH_RESOURCE_KEY,
            )
        )
        assert grant is not None
        grant.enabled = False


def _seed_artifact(app, tenant: dict[str, str], suffix: str) -> str:
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug=f"publishing-{suffix}-{uuid.uuid4().hex[:8]}",
            display_name="Publishing fixture model",
            provider_key="fixture",
            billing_mode="per_item",
            capability_version=1,
            active=True,
        )
        session.add(model)
        session.flush()
        task = GenerationTask(
            company_id=tenant["company_id"],
            user_id=tenant["user_id"],
            model_id=model.id,
            idempotency_key=f"generation-{suffix}-{uuid.uuid4().hex}",
            request_fingerprint="a" * 64,
            status=TaskStatus.SUCCEEDED,
            request_payload={"mode": "text_to_video", "output_count": 1},
            quote_cents=1,
            pricing_snapshot={},
            capability_snapshot={},
            reserved_cents=0,
            actual_cost_cents=1,
            output_artifacts=[],
        )
        session.add(task)
        session.flush()
        artifact = TaskArtifact(
            company_id=tenant["company_id"],
            task_id=task.id,
            asset_id=f"stored-{suffix}.mp4",
            position=0,
            media_type="video",
            content_type="video/mp4",
            size_bytes=1024,
            sha256="b" * 64,
        )
        session.add(artifact)
        session.flush()
        return artifact.id


def _connection(client, tenant, headers, display_name="Mock destination"):
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/publishing/connections",
        headers=headers,
        json={"provider": "mock", "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _job_payload(artifact_id: str, connection_id: str, suffix: str) -> dict:
    return {
        "artifact_id": artifact_id,
        "connection_id": connection_id,
        "idempotency_key": f"publication-{suffix}",
        "title": "Product launch",
        "caption": "A durable generated product video",
        "scheduled_at": None,
        "timezone": "Asia/Shanghai",
    }


def test_publishing_requires_entitlement_and_mock_opt_in(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    connections_url = (
        f"/api/v1/companies/{company_id}/publishing/connections"
    )
    jobs_url = f"/api/v1/companies/{company_id}/publishing/jobs"

    # Revoking the feature blocks new external side effects, while historical
    # read and cleanup endpoints remain available.
    assert client.get(connections_url, headers=tenant_headers).status_code == 200
    assert client.get(jobs_url, headers=tenant_headers).status_code == 200
    denied_create = client.post(
        connections_url,
        headers=tenant_headers,
        json={"provider": "mock", "display_name": "No grant"},
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["code"] == "permission_denied"

    _enable_auto_publish(app, company_id)
    mock_disabled = client.post(
        connections_url,
        headers=tenant_headers,
        json={"provider": "mock", "display_name": "Explicit mock"},
    )
    assert mock_disabled.status_code == 404

    app.state.settings.publishing_mock_enabled = True
    invalid_provider = client.post(
        connections_url,
        headers=tenant_headers,
        json={"provider": "douyin", "display_name": "Not implemented"},
    )
    assert invalid_provider.status_code == 422
    created = client.post(
        connections_url,
        headers=tenant_headers,
        json={"provider": "mock", "display_name": " Explicit mock "},
    )
    assert created.status_code == 201, created.text
    assert created.json()["provider"] == "mock"
    assert created.json()["display_name"] == "Explicit mock"
    assert created.json()["external_account_id"].startswith("mock-")
    assert client.get("/health/ready").status_code == 503
    app.state.settings.publishing_worker_enabled = True
    assert client.get("/health/ready").status_code == 200


def test_publication_job_idempotency_approval_cancel_and_audit(
    client, app, tenant, tenant_headers
):
    app.state.settings.publishing_mock_enabled = True
    _enable_auto_publish(app, tenant["company_id"])
    artifact_id = _seed_artifact(app, tenant, "lifecycle")
    connection = _connection(client, tenant, tenant_headers)
    jobs_url = (
        f"/api/v1/companies/{tenant['company_id']}/publishing/jobs"
    )
    payload = _job_payload(artifact_id, connection["id"], "stable-key")

    created = client.post(
        jobs_url,
        headers={**tenant_headers, "X-Request-ID": "publish-create"},
        json=payload,
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "pending_approval"
    assert job["task_artifact_id"] == artifact_id
    assert job["attempt_count"] == 0

    replay = client.post(jobs_url, headers=tenant_headers, json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == job["id"]
    mismatch = client.post(
        jobs_url,
        headers=tenant_headers,
        json={**payload, "caption": "Different payload"},
    )
    assert mismatch.status_code == 409

    approved = client.post(
        f"{jobs_url}/{job['id']}/approve",
        headers={**tenant_headers, "X-Request-ID": "publish-approve"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "queued"
    assert approved.json()["approved_by_user_id"] == tenant["user_id"]
    assert approved.json()["next_attempt_at"] is not None
    second_approval = client.post(
        f"{jobs_url}/{job['id']}/approve", headers=tenant_headers
    )
    assert second_approval.status_code == 409

    detail = client.get(f"{jobs_url}/{job['id']}", headers=tenant_headers)
    assert detail.status_code == 200
    assert detail.json()["attempts"] == []
    job_page = client.get(jobs_url, headers=tenant_headers).json()
    assert job_page["total"] == 1
    assert [item["id"] for item in job_page["items"]] == [job["id"]]

    cancelled = client.post(
        f"{jobs_url}/{job['id']}/cancel",
        headers={**tenant_headers, "X-Request-ID": "publish-cancel"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_by_user_id"] == tenant["user_id"]
    assert client.post(
        f"{jobs_url}/{job['id']}/retry", headers=tenant_headers
    ).status_code == 409

    disabled = client.delete(
        f"/api/v1/companies/{tenant['company_id']}/publishing/"
        f"connections/{connection['id']}",
        headers={**tenant_headers, "X-Request-ID": "publish-disable"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"
    unusable = client.post(
        jobs_url,
        headers=tenant_headers,
        json={
            **payload,
            "idempotency_key": "publication-disabled-connection",
        },
    )
    assert unusable.status_code == 409

    with app.state.session_factory() as session:
        actions = set(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.request_id.in_(
                        {
                            "publish-create",
                            "publish-approve",
                            "publish-cancel",
                            "publish-disable",
                        }
                    )
                )
            ).all()
        )
    assert actions == {
        "publishing.connection.disable",
        "publishing.job.approve",
        "publishing.job.cancel",
        "publishing.job.create",
    }


def test_scheduled_retry_unknown_and_cross_tenant_isolation(
    client, app, tenant, tenant_headers
):
    app.state.settings.publishing_mock_enabled = True
    _enable_auto_publish(app, tenant["company_id"])
    artifact_id = _seed_artifact(app, tenant, "scheduled")
    connection = _connection(client, tenant, tenant_headers, "Schedule account")
    jobs_url = (
        f"/api/v1/companies/{tenant['company_id']}/publishing/jobs"
    )
    scheduled_for = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=2)
    payload = {
        **_job_payload(artifact_id, connection["id"], "scheduled"),
        "scheduled_at": scheduled_for.isoformat(),
    }
    created = client.post(jobs_url, headers=tenant_headers, json=payload)
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    approved = client.post(
        f"{jobs_url}/{job_id}/approve", headers=tenant_headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "scheduled"

    with app.state.session_factory.begin() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        job.status = PublicationJobStatus.FAILED
        job.attempt_count = 1
        job.next_attempt_at = None
        job.error_code = "provider_busy"
        job.error_message = "Provider was busy"
    retried = client.post(f"{jobs_url}/{job_id}/retry", headers=tenant_headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "scheduled"
    assert retried.json()["error_code"] is None

    with app.state.session_factory.begin() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        job.status = PublicationJobStatus.SUBMISSION_UNKNOWN
        job.next_attempt_at = None
    unknown_retry = client.post(
        f"{jobs_url}/{job_id}/retry", headers=tenant_headers
    )
    assert unknown_retry.status_code == 409
    assert "never retried" in unknown_retry.json()["detail"]

    _disable_auto_publish(app, tenant["company_id"])
    assert client.get(f"{jobs_url}/{job_id}", headers=tenant_headers).status_code == 200
    reconciled_failed = client.post(
        f"{jobs_url}/{job_id}/reconcile",
        headers={**tenant_headers, "X-Request-ID": "publish-reconcile-failed"},
        json={
            "outcome": "failed",
            "error_code": "provider_confirmed_absent",
            "error_message": "Provider support confirmed that no post exists",
        },
    )
    assert reconciled_failed.status_code == 200, reconciled_failed.text
    assert reconciled_failed.json()["status"] == "failed"
    assert reconciled_failed.json()["error_code"] == "provider_confirmed_absent"
    replay_reconciliation = client.post(
        f"{jobs_url}/{job_id}/reconcile",
        headers=tenant_headers,
        json={
            "outcome": "failed",
            "error_code": "provider_confirmed_absent",
            "error_message": "Provider support confirmed that no post exists",
        },
    )
    assert replay_reconciliation.status_code == 200
    assert client.post(
        f"{jobs_url}/{job_id}/retry", headers=tenant_headers
    ).status_code == 403
    _enable_auto_publish(app, tenant["company_id"])
    assert client.post(
        f"{jobs_url}/{job_id}/retry", headers=tenant_headers
    ).status_code == 200

    published_artifact_id = _seed_artifact(app, tenant, "reconcile-published")
    published_job = client.post(
        jobs_url,
        headers=tenant_headers,
        json=_job_payload(
            published_artifact_id,
            connection["id"],
            "reconcile-published",
        ),
    )
    assert published_job.status_code == 201, published_job.text
    published_job_id = published_job.json()["id"]
    with app.state.session_factory.begin() as session:
        job = session.get(PublicationJob, published_job_id)
        assert job is not None
        job.status = PublicationJobStatus.SUBMISSION_UNKNOWN
        job.lease_owner = "stale-worker"
        job.lease_token = "c" * 32
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    active_lease = client.post(
        f"{jobs_url}/{published_job_id}/reconcile",
        headers=tenant_headers,
        json={
            "outcome": "published",
            "external_post_id": "external-42",
            "external_post_url": "https://social.example/posts/42",
        },
    )
    assert active_lease.status_code == 409
    with app.state.session_factory.begin() as session:
        job = session.get(PublicationJob, published_job_id)
        assert job is not None
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
    insecure_url = client.post(
        f"{jobs_url}/{published_job_id}/reconcile",
        headers=tenant_headers,
        json={
            "outcome": "published",
            "external_post_id": "external-42",
            "external_post_url": "http://social.example/posts/42",
        },
    )
    assert insecure_url.status_code == 422
    reconciled_published = client.post(
        f"{jobs_url}/{published_job_id}/reconcile",
        headers={**tenant_headers, "X-Request-ID": "publish-reconcile-published"},
        json={
            "outcome": "published",
            "external_post_id": "external-42",
            "external_post_url": "https://social.example/posts/42",
        },
    )
    assert reconciled_published.status_code == 200, reconciled_published.text
    assert reconciled_published.json()["status"] == "published"
    assert reconciled_published.json()["external_post_id"] == "external-42"
    assert client.post(
        f"{jobs_url}/{published_job_id}/reconcile",
        headers=tenant_headers,
        json={
            "outcome": "published",
            "external_post_id": "external-42",
            "external_post_url": "https://social.example/posts/42",
        },
    ).status_code == 200

    other = bootstrap(client, "publishing-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    _enable_auto_publish(app, other["company_id"])
    other_connection = _connection(client, other, other_headers, "Other account")
    cross_artifact = client.post(
        f"/api/v1/companies/{other['company_id']}/publishing/jobs",
        headers=other_headers,
        json=_job_payload(artifact_id, other_connection["id"], "cross-artifact"),
    )
    assert cross_artifact.status_code == 404
    cross_job = client.get(
        f"/api/v1/companies/{other['company_id']}/publishing/jobs/{job_id}",
        headers=other_headers,
    )
    assert cross_job.status_code == 404
    cross_connection = client.delete(
        f"/api/v1/companies/{other['company_id']}/publishing/connections/"
        f"{connection['id']}",
        headers=other_headers,
    )
    assert cross_connection.status_code == 404


def test_operator_defaults_separate_account_management_from_job_management(
    client, app, tenant, tenant_headers
):
    app.state.settings.publishing_mock_enabled = True
    _enable_auto_publish(app, tenant["company_id"])
    owner_artifact_id = _seed_artifact(app, tenant, "operator-owner")
    connection = _connection(client, tenant, tenant_headers, "Owner connection")
    member_response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={
            "email": "publishing-operator@example.com",
            "display_name": "Publishing Operator",
        },
    )
    assert member_response.status_code == 201, member_response.text
    member = member_response.json()
    member_headers = {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": member["user_id"],
    }
    connections_url = (
        f"/api/v1/companies/{tenant['company_id']}/publishing/connections"
    )
    assert client.get(connections_url, headers=member_headers).status_code == 200
    assert client.post(
        connections_url,
        headers=member_headers,
        json={"provider": "mock", "display_name": "Forbidden"},
    ).status_code == 403
    jobs_url = f"/api/v1/companies/{tenant['company_id']}/publishing/jobs"
    overreach = client.post(
        jobs_url,
        headers=member_headers,
        json=_job_payload(
            owner_artifact_id, connection["id"], "operator-overreach"
        ),
    )
    assert overreach.status_code == 404

    member_artifact_id = _seed_artifact(
        app,
        {
            "company_id": tenant["company_id"],
            "user_id": member["user_id"],
        },
        "operator-own",
    )
    created = client.post(
        jobs_url,
        headers=member_headers,
        json=_job_payload(member_artifact_id, connection["id"], "operator-own"),
    )
    assert created.status_code == 201, created.text
    assert created.json()["created_by_user_id"] == member["user_id"]
    assert client.get(jobs_url, headers=member_headers).status_code == 200

    with app.state.session_factory.begin() as session:
        session.add(
            MemberPermissionOverride(
                membership_id=member["membership_id"],
                permission_code="reports.read",
                effect=PermissionEffect.ALLOW,
            )
        )
    reporter_job = client.post(
        jobs_url,
        headers=member_headers,
        json=_job_payload(
            owner_artifact_id, connection["id"], "operator-reporter"
        ),
    )
    assert reporter_job.status_code == 201, reporter_job.text


def test_platform_admin_can_create_company_publication_job(
    client, app, tenant, tenant_headers
):
    app.state.settings.publishing_mock_enabled = True
    _enable_auto_publish(app, tenant["company_id"])
    artifact_id = _seed_artifact(app, tenant, "platform-admin")
    connection = _connection(client, tenant, tenant_headers, "Admin destination")
    admin = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": "publishing-platform-admin@example.com",
            "display_name": "Publishing Platform Admin",
        },
    )
    assert admin.status_code == 201, admin.text
    response = client.post(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/publishing/jobs",
        headers={"X-Platform-Admin-User-ID": admin.json()["user_id"]},
        json=_job_payload(artifact_id, connection["id"], "platform-admin"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_by_user_id"] == admin.json()["user_id"]


def test_production_rejects_publishing_mock_configuration():
    with pytest.raises(
        ValidationError,
        match="PUBLISHING_MOCK_ENABLED cannot be enabled",
    ):
        production_settings(publishing_mock_enabled=True)

    with pytest.raises(
        ValidationError,
        match="requires at least one PUBLISHING_ADAPTERS",
    ):
        production_settings(publishing_worker_enabled=True)


def test_publication_schedule_requires_valid_iana_timezone_and_matching_offset(
    client, app, tenant, tenant_headers
):
    app.state.settings.publishing_mock_enabled = True
    _enable_auto_publish(app, tenant["company_id"])
    artifact_id = _seed_artifact(app, tenant, "timezone")
    connection = _connection(client, tenant, tenant_headers, "Timezone account")
    jobs_url = f"/api/v1/companies/{tenant['company_id']}/publishing/jobs"
    base = _job_payload(artifact_id, connection["id"], "timezone-base")

    invalid_zone = client.post(
        jobs_url,
        headers=tenant_headers,
        json={**base, "idempotency_key": "timezone-invalid-zone", "timezone": "Mars/Olympus"},
    )
    assert invalid_zone.status_code == 422
    mismatched_offset = client.post(
        jobs_url,
        headers=tenant_headers,
        json={
            **base,
            "idempotency_key": "timezone-offset-mismatch",
            "scheduled_at": "2026-08-07T10:00:00+00:00",
            "timezone": "Asia/Shanghai",
        },
    )
    assert mismatched_offset.status_code == 422
    nonexistent_dst_time = client.post(
        jobs_url,
        headers=tenant_headers,
        json={
            **base,
            "idempotency_key": "timezone-dst-gap",
            "scheduled_at": "2026-03-08T02:30:00-05:00",
            "timezone": "America/New_York",
        },
    )
    assert nonexistent_dst_time.status_code == 422
    valid = client.post(
        jobs_url,
        headers=tenant_headers,
        json={
            **base,
            "idempotency_key": "timezone-valid-shanghai",
            "scheduled_at": "2026-08-07T10:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
    )
    assert valid.status_code == 201, valid.text
