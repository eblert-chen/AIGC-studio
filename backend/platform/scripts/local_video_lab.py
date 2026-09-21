"""Explicit, isolated local video lab; never an ordinary Platform bootstrap.

The factory adds lab identity plus one exact commercial-activation endpoint.
That transaction exclusively owns model publication, grants, fixed quotas and
both promotional test budgets; legacy standalone point-write paths fail closed.
The CLI never submits a provider probe or task.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))


KIND = "ai-video-local-video-lab"
TARGETS = {
    "gateway_base": "http://127.0.0.1:18480",
    "platform_base": "http://127.0.0.1:18420",
    "relay_base": "http://127.0.0.1:18430",
    "frontend_base": "http://127.0.0.1:14178",
}
IDENTITY_PATH = "/internal/local-video-lab/identity"
POINTS_PATH = "/internal/local-video-lab/test-points"
PERSONAL_IDENTITY_PATH = "/internal/local-video-lab/personal-identity"
PERSONAL_POINTS_PATH = "/internal/local-video-lab/personal-test-points"
ACTIVATION_PATH = "/internal/local-video-lab/commercial-activation"
LOGIN_PATH = "/internal/local-video-lab/login"
MARKER_ACTION = "local_video_lab.initialize"
TOKEN_ENV = "LOCAL_VIDEO_LAB_BOOTSTRAP_TOKEN"
MANIFEST_ENV = "LOCAL_VIDEO_LAB_MANIFEST"
LAB_HEADERS = ("X-Local-Video-Lab-ID", "X-Local-Video-Lab-Nonce")


class LabError(ValueError):
    """Safe diagnostic: never include API bodies, tokens or connection URLs."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def manifest_sha256(manifest: dict) -> str:
    return "sha256:" + hashlib.sha256(_canonical(manifest).encode("utf-8")).hexdigest()


def _keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise LabError(f"Invalid {label} fields")


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LabError(f"{label} must be a UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LabError(f"Invalid {label}") from exc


def validate_manifest(value: Any) -> dict:
    _keys(value, {"schema_version", "kind", "lab_id", "instance_nonce", "provider_mode",
                  "created_at_utc", "targets", "isolation", "budget", "paid_probe_approval"}, "manifest")
    if value["schema_version"] != 1 or value["kind"] != KIND:
        raise LabError("Unsupported local lab manifest")
    mode = value["provider_mode"]
    lab_id = value["lab_id"]
    if mode not in {"mock", "live"} or not isinstance(lab_id, str) or not re.fullmatch(rf"{mode}-[0-9a-f]{{12}}", lab_id):
        raise LabError("Lab ID must bind its mock/live mode")
    if not isinstance(value["instance_nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["instance_nonce"]):
        raise LabError("Invalid local lab instance nonce")
    created = _utc(value["created_at_utc"], "created_at_utc")
    if created.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
        raise LabError("Lab manifest creation time is in the future")
    if value["targets"] != TARGETS:
        raise LabError("Only the four dedicated loopback lab ports are allowed")
    isolation = value["isolation"]
    _keys(isolation, {"compose_project", "platform_database", "relay_service_base"}, "isolation")
    if isolation["compose_project"] != f"ai-video-lab-{lab_id}":
        raise LabError("Compose project does not match this lab")
    expected_db = {"host": "platform-db-lab", "port": 5432, "database": "video_lab_" + lab_id.replace("-", "_")}
    if isolation["platform_database"] != expected_db:
        raise LabError("Only the exact dedicated Platform lab database is allowed")
    if isolation["relay_service_base"] != "http://relay-lab:3000":
        raise LabError("Only the isolated Relay service origin is allowed")
    budget = value["budget"]
    _keys(budget, {"test_points", "unit_price_points_per_second", "call_quota", "concurrency_limit"}, "budget")
    for field, maximum in {"test_points": 100_000, "unit_price_points_per_second": 100,
                           "call_quota": 100, "concurrency_limit": 2}.items():
        if type(budget[field]) is not int or not 1 <= budget[field] <= maximum:
            raise LabError(f"Invalid bounded test budget: {field}")
    approval = value["paid_probe_approval"]
    if mode == "mock" and approval is not None:
        raise LabError("A mock lab must not claim paid-probe approval")
    if approval is not None:
        _keys(approval, {"actor", "reason", "operation_id", "approved_at_utc"}, "paid_probe_approval")
        for field in ("actor", "reason", "operation_id"):
            if not isinstance(approval[field], str) or not 3 <= len(approval[field]) <= 240 or approval[field] != approval[field].strip():
                raise LabError(f"Invalid paid-probe approval {field}")
        if _utc(approval["approved_at_utc"], "approved_at_utc").timestamp() > datetime.now(timezone.utc).timestamp() + 300:
            raise LabError("Paid-probe approval is in the future")
    return json.loads(_canonical(value))


def load_manifest(path: str | Path) -> dict:
    source = Path(path)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise LabError("Manifest must be an existing absolute regular file")
    if source.stat().st_size > 32_768:
        raise LabError("Lab manifest is too large")
    try:
        return validate_manifest(json.loads(source.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabError("Cannot read a valid lab manifest") from exc


def allowed_model_contracts() -> dict[str, dict]:
    """Single-source software contracts, not evidence of provider acceptance."""
    from platform_api.services.task_admission import TaskCapabilityAdmission

    root = Path(__file__).resolve().parents[2] / "new-api-relay" / "generationprofile"
    result = {}
    now = datetime.now(timezone.utc)
    for name in ("seedance_models.v1.json", "minimax_h3_models.v1.json"):
        source = json.loads((root / name).read_text(encoding="utf-8"))
        for item in source["models"]:
            if item.get("new_routes_allowed") is not True or item.get("lifecycle") != "acceptance_candidate":
                continue
            if item.get("eos_at") and _utc(item["eos_at"], "eos_at") <= now:
                continue
            result[item["public_model_id"]] = TaskCapabilityAdmission.validate_catalog(
                {"generation": item["capability"]}, require_usable=True)
    if not result:
        raise LabError("No eligible versioned video model contracts")
    return result


def _require_live_approval(manifest: dict) -> None:
    if manifest["provider_mode"] == "live" and manifest["paid_probe_approval"] is None:
        raise LabError("Live activation requires a separate lab and explicit paid-probe approval; this CLI never probes")


def _names(manifest: dict) -> dict[str, str]:
    lab_id = manifest["lab_id"]
    mode_label = "模拟供应商" if manifest["provider_mode"] == "mock" else "真实供应商·独立测试"
    return {
        "company_name": f"本地视频联调·{mode_label}·{lab_id}",
        "owner_email": f"{lab_id}-owner@local-video-lab.example.com",
        "owner_display_name": "本地视频联调测试用户",
        "personal_email": f"{lab_id}-personal@local-video-lab.example.com",
        "personal_display_name": "本地视频联调个人测试用户",
        "admin_email": f"{lab_id}-admin@local-video-lab.example.com",
    }


def validate_factory_settings(settings: Any, manifest: dict, token: str) -> None:
    from sqlalchemy.engine import make_url
    from platform_api.config import runtime_settings_are_protected

    if settings.environment != "development" or runtime_settings_are_protected(settings):
        raise LabError("The lab factory is forbidden in protected runtimes")
    if not settings.enable_bootstrap or not settings.development_header_auth_enabled:
        raise LabError("The isolated lab requires explicit development bootstrap/header authentication")
    if getattr(settings, "oidc_enabled", False) or getattr(settings, "platform_owner_user_ids", []):
        raise LabError("The lab must not inherit production IdP or owner identities")
    if getattr(settings, "frontend_origin", None) not in {TARGETS["gateway_base"], TARGETS["frontend_base"]}:
        raise LabError("The lab requires its exact browser origin for normal CSRF protection")
    if len(token) < 32 or not settings.bootstrap_token or not hmac.compare_digest(token, settings.bootstrap_token):
        raise LabError("The lab bootstrap credential is missing or does not match")
    try:
        database = make_url(settings.database_url)
    except Exception as exc:
        raise LabError("Invalid lab database configuration") from exc
    expected = manifest["isolation"]["platform_database"]
    if (database.drivername != "postgresql+psycopg" or database.host != expected["host"]
            or (database.port or 5432) != expected["port"] or database.database != expected["database"]
            or database.username != "video_lab" or database.query):
        raise LabError("Refusing an existing, ordinary or unbound Platform database")
    relay_origin = manifest["isolation"]["relay_service_base"]
    backends = getattr(settings, "relay_backends", {})
    if backends:
        configured = backends.get(settings.relay_default_backend_id)
        if len(backends) != 1 or configured is None or str(configured.base_url).rstrip("/") != relay_origin:
            raise LabError("The lab must use exactly one isolated Relay backend")
    elif settings.relay_base_url != relay_origin:
        raise LabError("Platform Relay client must target the isolated Relay service")
    if settings.relay_base_url not in {None, relay_origin} or getattr(settings, "relay_operations_base_url", None) not in {None, relay_origin}:
        raise LabError("Additional Relay clients must not escape this isolated lab")


def _ensure_database_marker(app: Any, manifest: dict) -> None:
    from sqlalchemy import literal, select, text
    from platform_api.database import Base
    from platform_api.models import AuditLog
    from platform_api.platform_admin_access_catalog import PLATFORM_ADMIN_PERMISSION_CATALOG
    from platform_api.services.audit import AuditService

    binding = {"kind": KIND, "lab_id": manifest["lab_id"], "instance_nonce": manifest["instance_nonce"],
               "provider_mode": manifest["provider_mode"], "manifest_sha256": manifest_sha256(manifest),
               "test_data_only": True, "cash_basis_cents": 0}
    with app.state.session_factory.begin() as session:
        if app.state.engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(1842018480)"))
        markers = session.scalars(select(AuditLog).where(AuditLog.action == MARKER_ACTION)).all()
        if markers:
            if len(markers) != 1 or markers[0].after_summary != binding:
                raise LabError("Database belongs to a different lab, mode or manifest; never reuse mock storage for live")
            return
        # A name alone is not isolation evidence. Refuse all pre-existing domain
        # data, including other audit events, before creating the durable marker.
        for table in Base.metadata.sorted_tables:
            if table.name == "permissions":  # Migration-owned static permission catalog.
                continue
            if table.name == "platform_admin_permissions":
                rows = session.execute(select(table)).mappings().all()
                expected = {spec.code: {"code": spec.code, "domain": spec.domain, "action": spec.action,
                                        "description": spec.description}
                            for spec in PLATFORM_ADMIN_PERMISSION_CATALOG}
                if not rows or {row["code"]: dict(row) for row in rows} == expected:
                    continue  # Exact immutable 0024/0043 catalog, never roles or assignments.
            if table.name == "resource_definitions":
                rows = session.execute(select(table)).mappings().all()
                if not rows or (len(rows) == 1 and rows[0]["id"] == "00000000-0000-4000-8000-000000000020"
                                and rows[0]["key"] == "feature.auto_publish"):
                    continue  # Exact static migration 0020 seed, not any customer grant.
            if table.name == "showcase_channels":
                rows = session.execute(select(table)).mappings().all()
                if not rows or (len(rows) == 1 and rows[0]["id"] == "home"
                                and rows[0]["draft_version"] == rows[0]["publication_version"] == 0
                                and rows[0]["current_release_id"] is None and rows[0]["updated_by_user_id"] is None):
                    continue  # Empty singleton from migration 0040, never published content.
            if session.scalar(select(literal(1)).select_from(table).limit(1)) is not None:
                raise LabError("Refusing non-empty storage without a matching durable lab marker")
        AuditService.append(session, actor_kind="system", actor_key="local-video-lab",
                            action=MARKER_ACTION, target_type="local_video_lab", target_id=manifest["lab_id"],
                            before_summary={}, after_summary=binding, request_id=f"lab-init-{manifest['lab_id']}")


def _identity(app: Any, manifest: dict) -> dict:
    from sqlalchemy import select
    from platform_api.models import Company, CompanyMembership, PersonalWorkspace, User

    names = _names(manifest)
    with app.state.session_factory() as session:
        admin = session.scalar(select(User).where(User.email == names["admin_email"]))
        owner = session.scalar(select(User).where(User.email == names["owner_email"]))
        personal_user = session.scalar(
            select(User).where(User.email == names["personal_email"])
        )
        personal_workspace = (
            session.scalar(
                select(PersonalWorkspace).where(
                    PersonalWorkspace.user_id == personal_user.id
                )
            )
            if personal_user is not None
            else None
        )
        companies = session.scalars(select(Company).where(Company.name == names["company_name"])).all()
        if len(companies) > 1:
            raise LabError("Ambiguous lab bootstrap; do not create another company")
        company = companies[0] if companies else None
        membership = session.scalar(select(CompanyMembership).where(
            CompanyMembership.company_id == company.id, CompanyMembership.user_id == owner.id,
        )) if company is not None and owner is not None else None
        if (company is None) != (owner is None) or (company is not None and membership is None):
            raise LabError("Incomplete lab ownership identity")
        if (personal_user is None) != (personal_workspace is None):
            raise LabError("Incomplete lab personal identity")
        return {
            "kind": KIND, "lab_id": manifest["lab_id"], "provider_mode": manifest["provider_mode"],
            "instance_nonce": manifest["instance_nonce"], "manifest_sha256": manifest_sha256(manifest),
            "storage_bound": True, "test_data_only": True,
            "company_id": company.id if company else None,
            "user_id": owner.id if owner else None,
            "membership_id": membership.id if membership else None,
            "admin_user_id": admin.id if admin and admin.is_platform_admin else None,
            "personal_user_id": personal_user.id if personal_user else None,
            "personal_workspace_id": (
                personal_workspace.id if personal_workspace else None
            ),
            "billing_version": company.billing_version if company else None,
        }


class TestPointsRequest(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")
    company_id: str


class PersonalTestPointsRequest(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")
    user_id: str


class LabCommercialActivationPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=36, max_length=36)
    model_id: str = Field(min_length=36, max_length=36)
    model_slug: str = Field(min_length=1, max_length=128)
    plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LabCommercialActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[LabCommercialActivationPlanRequest] = Field(
        min_length=1,
        max_length=100,
    )


class LabLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_kind: Literal["company", "personal"]


def install_lab_endpoints(app: Any, manifest: dict) -> Any:
    """Internal factory component; tests inject an isolated in-memory app here."""
    from platform_api.dependencies import get_db, require_platform_admin
    from platform_api.relay_client import RelayPermanentError, RelayTemporaryError
    from platform_api.services.audit import AuditService
    from platform_api.services.model_commercial_release import (
        ModelCommercialReleaseService,
    )

    _ensure_database_marker(app, manifest)

    @app.exception_handler(LabError)
    async def lab_error_handler(_request: Request, error: LabError):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    def require_lab(request: Request) -> None:
        # Host-side requests arrive through this lab's Docker bridge gateway or
        # its fixed dual-network TCP edge, never an arbitrary container peer.
        # The orchestrator publishes these ports on 127.0.0.1 only. Never trust
        # forwarded host/address headers or arbitrary private address ranges.
        peer = request.client.host if request.client else ""
        try:
            local_peer = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local_peer = peer == "testclient"  # In-process ASGI tests, never a TCP peer.
        if not local_peer and peer not in {"11.254.93.1", "11.254.94.1", "11.254.93.5", "11.254.94.5"}:
            raise HTTPException(403, "Local lab requests must use its loopback publication")
        if request.headers.get("host") != urlsplit(TARGETS["platform_base"]).netloc:
            raise HTTPException(403, "Wrong local lab service origin")
        token = request.headers.get("X-Bootstrap-Token", "")
        if not token or not hmac.compare_digest(token, app.state.settings.bootstrap_token or ""):
            raise HTTPException(401, "Local lab authentication failed")
        if (request.headers.get(LAB_HEADERS[0]) != manifest["lab_id"]
                or request.headers.get(LAB_HEADERS[1]) != manifest["instance_nonce"]):
            raise HTTPException(409, "Local lab instance mismatch")

    @app.get(IDENTITY_PATH, dependencies=[Depends(require_lab)])
    def read_identity():
        return _identity(app, manifest)

    @app.post(PERSONAL_IDENTITY_PATH, dependencies=[Depends(require_lab)])
    def provision_personal_identity(
        request: Request,
        admin=Depends(require_platform_admin),
        session=Depends(get_db, scope="function"),
    ):
        from sqlalchemy import select
        from platform_api.models import User, UserAccountType
        from platform_api.services.personal import PersonalWorkspaceService

        identity = _identity(app, manifest)
        if admin.user_id != identity["admin_user_id"]:
            raise HTTPException(
                403,
                "Personal lab identity is restricted to this lab's administrator",
            )
        names = _names(manifest)
        user = session.scalar(
            select(User).where(User.email == names["personal_email"])
        )
        created = user is None
        if user is None:
            user = User(
                email=names["personal_email"],
                display_name=names["personal_display_name"],
                account_type=UserAccountType.PERSONAL,
            )
            session.add(user)
            session.flush()
        if (
            user.account_type != UserAccountType.PERSONAL
            or user.is_platform_admin
        ):
            raise HTTPException(409, "Local personal identity has changed")
        workspace = PersonalWorkspaceService.ensure(session, user_id=user.id)
        if created:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="local_video_lab.personal_identity",
                target_type="personal_workspace",
                target_id=workspace.id,
                before_summary={},
                after_summary={
                    "lab_id": manifest["lab_id"],
                    "test_data_only": True,
                    "user_id": user.id,
                },
                request_id=request.state.request_id,
            )
        return {
            "test_data_only": True,
            "user_id": user.id,
            "workspace_id": workspace.id,
            "created": created,
        }

    @app.post(POINTS_PATH, dependencies=[Depends(require_lab)])
    def grant_test_points(
        _body: TestPointsRequest,
        _admin=Depends(require_platform_admin),
    ):
        _require_live_approval(manifest)
        raise HTTPException(
            409,
            "Standalone company test-point grants are disabled; exact commercial "
            "activation owns the complete non-cash budget",
        )

    @app.post(PERSONAL_POINTS_PATH, dependencies=[Depends(require_lab)])
    def grant_personal_test_points(
        _body: PersonalTestPointsRequest,
        _admin=Depends(require_platform_admin),
    ):
        _require_live_approval(manifest)
        raise HTTPException(
            409,
            "Standalone personal test-point grants are disabled; exact commercial "
            "activation owns the complete non-cash budget",
        )

    def activation_intent(
        body: LabCommercialActivationRequest,
        *,
        admin_user_id: str,
    ) -> dict[str, Any]:
        identity = _identity(app, manifest)
        if (
            identity.get("admin_user_id") != admin_user_id
            or not identity.get("company_id")
            or not identity.get("user_id")
            or not identity.get("personal_user_id")
            or not identity.get("personal_workspace_id")
        ):
            raise HTTPException(
                403,
                "Commercial activation is restricted to this lab's exact scopes",
            )
        return ModelCommercialReleaseService.local_lab_activation_intent(
            lab_id=manifest["lab_id"],
            manifest_sha256=manifest_sha256(manifest),
            provider_mode=manifest["provider_mode"],
            company_id=identity["company_id"],
            company_user_id=identity["user_id"],
            personal_user_id=identity["personal_user_id"],
            personal_workspace_id=identity["personal_workspace_id"],
            test_points=manifest["budget"]["test_points"],
            unit_price_points_per_second=manifest["budget"][
                "unit_price_points_per_second"
            ],
            call_quota=manifest["budget"]["call_quota"],
            concurrency_limit=manifest["budget"]["concurrency_limit"],
            plans=tuple(item.model_dump() for item in body.plans),
        )

    @app.get(ACTIVATION_PATH, dependencies=[Depends(require_lab)])
    def read_commercial_activation(
        admin=Depends(require_platform_admin),
        session=Depends(get_db, scope="function"),
    ):
        identity = _identity(app, manifest)
        if identity.get("admin_user_id") != admin.user_id:
            raise HTTPException(
                403,
                "Commercial activation is restricted to this lab's administrator",
            )
        receipt = (
            ModelCommercialReleaseService.read_local_lab_activation_receipt_for_lab(
                session,
                lab_id=manifest["lab_id"],
                manifest_sha256=manifest_sha256(manifest),
            )
        )
        if receipt is None:
            raise HTTPException(409, "Local lab has no completed commercial activation")
        return receipt

    @app.post(ACTIVATION_PATH, dependencies=[Depends(require_lab)])
    def activate_commercial_plan_set(
        body: LabCommercialActivationRequest,
        request: Request,
        admin=Depends(require_platform_admin),
        session=Depends(get_db, scope="function"),
    ):
        _require_live_approval(manifest)
        intent = activation_intent(body, admin_user_id=admin.user_id)
        replay = ModelCommercialReleaseService.read_local_lab_activation_receipt(
            session,
            intent=intent,
        )
        if replay is not None:
            return replay
        client = app.state.relay_client
        if client is None:
            raise HTTPException(503, "Relay client is not configured")
        try:
            read = client.get_model_catalog(request_id=request.state.request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(503, "Relay model catalog is temporarily unavailable") from exc
        except RelayPermanentError as exc:
            raise HTTPException(502, "Relay model catalog response is invalid") from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(502, "Relay model catalog response is incomplete")
        evidence_reader = getattr(
            app.state,
            "read_relay_model_release_evidence",
            None,
        )
        if not callable(evidence_reader):
            raise HTTPException(503, "Relay release evidence reader is unavailable")
        release_evidence, _ = evidence_reader(
            request_id=request.state.request_id,
            required=True,
        )
        if release_evidence is None:  # pragma: no cover - required reader fails first
            raise HTTPException(503, "Relay release evidence is unavailable")
        return ModelCommercialReleaseService.activate_local_lab_exact(
            session,
            intent=intent,
            catalog=read.catalog,
            release_evidence=release_evidence,
            approved_by_user_id=admin.user_id,
            request_id=request.state.request_id,
        )

    @app.post(LOGIN_PATH, dependencies=[Depends(require_lab)])
    def login_lab_owner(
        body: LabLoginRequest,
        request: Request,
        session=Depends(get_db, scope="function"),
    ):
        from sqlalchemy import select
        from platform_api.auth import OidcIdTokenClaims
        from platform_api.models import (
            Company,
            CompanyMembership,
            CompanyStatus,
            ExternalIdentity,
            MembershipStatus,
            PersonalWorkspace,
            User,
            UserAccountType,
            UserStatus,
        )
        from platform_api.routers.authentication import _set_session_cookies
        from platform_api.services.authentication import SessionService

        identity = _identity(app, manifest)
        company = None
        personal_workspace = None
        if body.scope_kind == "personal":
            if (
                not identity["personal_user_id"]
                or not identity["personal_workspace_id"]
            ):
                raise HTTPException(
                    409, "This lab's personal identity has not been provisioned"
                )
            user = session.get(User, identity["personal_user_id"])
            personal_workspace = session.get(
                PersonalWorkspace, identity["personal_workspace_id"]
            )
            if (
                user is None
                or user.status != UserStatus.ACTIVE
                or user.account_type != UserAccountType.PERSONAL
                or personal_workspace is None
                or not personal_workspace.active
                or personal_workspace.user_id != user.id
            ):
                raise HTTPException(403, "This lab's personal identity is not active")
            subject = "isolated-personal-user"
        else:
            if not identity["company_id"] or not identity["user_id"]:
                raise HTTPException(
                    409, "This lab's company owner has not been provisioned"
                )
            user = session.get(User, identity["user_id"])
            company = session.get(Company, identity["company_id"])
            membership = session.get(
                CompanyMembership, identity["membership_id"]
            )
            if (
                user is None
                or user.status != UserStatus.ACTIVE
                or user.account_type != UserAccountType.COMPANY
                or company is None
                or company.status != CompanyStatus.ACTIVE
                or membership is None
                or membership.status != MembershipStatus.ACTIVE
            ):
                raise HTTPException(403, "This lab's company owner is not active")
            subject = "isolated-company-owner"
        now = datetime.now(timezone.utc)
        # Explicitly local identity provenance, never fabricated IdP or MFA proof.
        issuer = f"urn:ai-video:local-video-lab:{manifest['lab_id']}"
        external = session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == issuer,
                ExternalIdentity.subject == subject,
            )
        )
        if external is None:
            external = ExternalIdentity(
                user_id=user.id,
                issuer=issuer,
                subject=subject,
                email_at_link=user.email,
                last_login_at=now,
            )
            session.add(external)
            session.flush()
        if external.user_id != user.id:
            raise HTTPException(409, "Local identity binding has changed")
        ttl = min(app.state.settings.auth_session_ttl_seconds, 3600)
        claims = OidcIdTokenClaims(issuer=issuer, subject=external.subject, email=user.email,
                                  display_name=user.display_name, issued_at=now.timestamp(),
                                  expires_at=now.timestamp() + ttl, authentication_time=now.timestamp(),
                                  authentication_methods=("local_lab_bootstrap",))
        auth_session, raw_session, raw_csrf = SessionService.create(
            session, user=user, identity=external, claims=claims, ttl_seconds=ttl,
            user_agent="isolated-local-video-lab-gateway", pepper=app.state.settings.jwt_signing_secret,
            ip_hash=None, request_id=request.state.request_id)
        AuditService.append(session, actor_user_id=user.id, action="local_video_lab.login",
                            target_type="auth_session", target_id=auth_session.id, before_summary={},
                            after_summary={"lab_id": manifest["lab_id"], "test_data_only": True,
                                           "identity_provenance": "local_lab_bootstrap",
                                           "scope_kind": body.scope_kind,
                                           "company_id": company.id if company else None,
                                           "personal_workspace_id": (
                                               personal_workspace.id
                                               if personal_workspace else None
                                           )},
                            request_id=request.state.request_id)
        response = JSONResponse({"lab_id": manifest["lab_id"], "test_data_only": True,
                                 "scope_kind": body.scope_kind,
                                 "company_id": company.id if company else None,
                                 "personal_workspace_id": (
                                     personal_workspace.id
                                     if personal_workspace else None
                                 ), "user_id": user.id,
                                 "identity_provenance": "local_lab_bootstrap"}, headers={"Cache-Control": "no-store"})
        _set_session_cookies(response, raw_session=raw_session, raw_csrf=raw_csrf, max_age=ttl)
        return response

    return app


def lab_browser_settings(settings: Any, manifest: dict, requested_origin: str | None = None):
    """Attach a local cookie/CSRF origin without claiming an OIDC configuration.

Normal Settings deliberately couples FRONTEND_ORIGIN to complete OIDC config.
This isolated factory authenticates only its explicit bootstrap identity, so
the extra local origin belongs to this app's settings copy, not global config.
"""
    origin = requested_origin or manifest["targets"]["gateway_base"]
    if origin not in {TARGETS["gateway_base"], TARGETS["frontend_base"]}:
        raise LabError("The local browser origin must be one of the exact lab origins")
    return settings.model_copy(update={"frontend_origin": origin})


def create_lab_app():
    """uvicorn scripts.local_video_lab:create_lab_app --factory --host 0.0.0.0 --port 8000"""
    from platform_api.config import get_settings

    manifest = load_manifest(os.environ.get(MANIFEST_ENV, ""))
    try:
        configured = get_settings("platform-api")
    except Exception:
        # Settings errors may include input values. Never log secret material.
        raise LabError("Invalid local lab runtime settings; keep ordinary OIDC configuration disabled") from None
    settings = lab_browser_settings(configured, manifest, os.environ.get("LOCAL_VIDEO_LAB_BROWSER_ORIGIN"))
    validate_factory_settings(settings, manifest, os.environ.get(TOKEN_ENV, ""))
    # main creates its default app on import: validate the selected database and
    # runtime BEFORE importing it, so even that normal factory stays lab-bound.
    from platform_api.main import create_app

    return install_lab_endpoints(create_app(settings=settings), manifest)


class LabApi:
    def __init__(self, manifest: dict, token: str, client: Any = None):
        if len(token) < 32:
            raise LabError("The isolated lab bootstrap credential is missing")
        self.manifest = manifest
        self.client = client or httpx.Client(base_url=TARGETS["platform_base"], timeout=60,
                                            follow_redirects=False, trust_env=False)
        self.owns_client = client is None
        self.headers = {"X-Bootstrap-Token": token, LAB_HEADERS[0]: manifest["lab_id"],
                        LAB_HEADERS[1]: manifest["instance_nonce"]}

    def request(self, method: str, path: str, body: dict | None = None, *, admin: str | None = None,
                identity: dict | None = None):
        if not path.startswith(("/api/v1/", "/internal/local-video-lab/")) or "?" in path:
            raise LabError("Unsupported lab API path")
        headers = dict(self.headers)
        if admin:
            headers["X-Platform-Admin-User-ID"] = admin
        if identity is not None:
            company_id = identity.get("company_id")
            personal_workspace_id = identity.get("personal_workspace_id")
            user_id = identity.get("user_id")
            if (
                not isinstance(user_id, str)
                or not user_id
                or bool(company_id) == bool(personal_workspace_id)
            ):
                raise LabError(
                    "Lab API principal must select exactly one company or personal scope"
                )
            headers["X-User-ID"] = user_id
            if company_id:
                headers["X-Company-ID"] = company_id
        try:
            response = self.client.request(method, path, headers=headers, **({"json": body} if body is not None else {}))
        except httpx.HTTPError as exc:
            raise LabError(f"Lab API transport failed during {method} {path}; rerun to reconcile, not blind retry") from exc
        if response.status_code not in {200, 201}:
            raise LabError(f"Lab API rejected {method} {path} (HTTP {response.status_code})")
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise LabError("Invalid lab API response; no acceptance is inferred") from exc

    def identity(self) -> dict:
        value = self.request("GET", IDENTITY_PATH)
        expected = {"kind": KIND, "lab_id": self.manifest["lab_id"], "provider_mode": self.manifest["provider_mode"],
                    "instance_nonce": self.manifest["instance_nonce"], "manifest_sha256": manifest_sha256(self.manifest),
                    "storage_bound": True, "test_data_only": True}
        if not isinstance(value, dict) or any(value.get(key) != field for key, field in expected.items()):
            raise LabError("API is not the exact durable isolated lab; no writes authorized")
        return value

    def close(self):
        if self.owns_client:
            self.client.close()


def _select_lab_relay_items(evidence: dict, expected: dict[str, dict]) -> dict[str, dict]:
    items = {item["relay_model_id"]: item for item in evidence["items"]}
    missing = set(expected) - set(items)
    unexpected_routable = {
        slug
        for slug, item in items.items()
        if slug not in expected
        and not (
            item.get("route_evidence_status") == "blocked"
            and item.get("model_release_id") is None
            and item.get("model_release_revision") is None
            and item.get("route_count") == 0
            and item.get("enabled_route_count") == 0
            and item.get("accepted_route_count") == 0
            and item.get("fresh_test_count") == 0
            and item.get("latest_successful_test_at") is None
            and item.get("platform_active") is not True
            and item.get("approved_revision") is None
            and item.get("requires_approval") is True
        )
    }
    if missing or unexpected_routable:
        raise LabError("Relay catalog differs from the eligible versioned model set; no automatic expansion")
    # Code-reviewed candidate-only models may remain visible in the Relay
    # catalog, but this lab never approves, publishes, prices or grants them.
    return {slug: items[slug] for slug in expected}


_COMMERCIAL_VIDEO_MODES = frozenset({"text_to_video", "image_to_video"})
_IMAGE_INPUT_ROLES = frozenset(
    {"reference_image", "first_frame", "last_frame"}
)
_IMAGE_TEMPORAL_CONTROLS = frozenset({"first_frame", "last_frame"})
_RESOLUTION_RANK = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "768p": 768,
    "1080p": 1080,
    "2k": 2000,
    "4k": 4000,
}


def _normalized_capability(value: dict) -> dict:
    result = json.loads(_canonical(value))
    for mode in result.get("modes", {}).values():
        if not mode.get("conditional_required_resource_keys"):
            mode.pop("conditional_required_resource_keys", None)
    return result


def _relay_capability_matches(
    observed: dict,
    expected: dict,
    *,
    provider_mode: str = "mock",
) -> bool:
    if provider_mode == "mock":
        return _normalized_capability(observed) == _normalized_capability(
            expected
        )
    if provider_mode != "live":
        return False
    try:
        from platform_api.services.task_admission import TaskCapabilityAdmission

        TaskCapabilityAdmission.validate_full_restriction(
            ceiling=expected,
            candidate=observed,
        )
    except Exception:
        return False
    expected_modes = _COMMERCIAL_VIDEO_MODES & set(
        expected.get("modes", {})
    )
    return set(observed.get("modes", {})) == expected_modes


def _commercial_override(capability: dict) -> dict:
    """Create one explicit T2V + image-only I2V sellable restriction."""
    if capability.get("schema_version") != 3:
        raise LabError("Local video commercial release requires capability schema v3")
    source_modes = capability.get("modes")
    if not isinstance(source_modes, dict):
        raise LabError("Relay capability modes are invalid")
    if not _COMMERCIAL_VIDEO_MODES <= set(source_modes):
        raise LabError("Relay route lacks the required T2V and I2V modes")
    modes: dict[str, dict] = {}
    for mode_name in sorted(_COMMERCIAL_VIDEO_MODES):
        source = source_modes[mode_name]
        if not isinstance(source, dict) or source.get("required_resource_keys"):
            raise LabError("Commercial video modes must not require enterprise resources")
        conditional = source.get("conditional_required_resource_keys", {})
        if not isinstance(conditional, dict) or any(conditional.values()):
            raise LabError("Commercial video modes contain conditional enterprise resources")
        limits = source.get("limits")
        if not isinstance(limits, dict):
            raise LabError("Commercial video mode limits are invalid")
        input_media_types = (
            ["image"] if mode_name == "image_to_video" else []
        )
        input_roles = (
            sorted(
                set(source.get("input_roles", [])) & _IMAGE_INPUT_ROLES
            )
            if mode_name == "image_to_video"
            else []
        )
        if mode_name == "image_to_video" and not input_roles:
            raise LabError("Image-to-video route lacks an explicit image input role")
        modes[mode_name] = {
            "input_media_types": input_media_types,
            "input_roles": input_roles,
            "temporal_controls": sorted(
                set(source.get("temporal_controls", []))
                & _IMAGE_TEMPORAL_CONTROLS
                & set(input_roles)
            ),
            "structured_inputs": sorted(
                set(source.get("structured_inputs", []))
                & {"director_shot_v1"}
            ),
            "supports_face": False,
            "required_resource_keys": [],
            "limits": {
                **json.loads(_canonical(limits)),
                "max_images": (
                    int(limits.get("max_images", 0))
                    if mode_name == "image_to_video"
                    else 0
                ),
                "max_videos": 0,
                "max_audio": 0,
            },
        }
    override = {"schema_version": 3, "modes": modes}
    return json.loads(_canonical(override))


def _effective_commercial_capability(
    capability: dict, override: dict
) -> dict:
    from platform_api.services.task_admission import TaskCapabilityAdmission

    return TaskCapabilityAdmission.effective_capabilities(
        capability_map={"generation": capability},
        config_override=override,
        strict_catalog=True,
        strict_override=True,
        require_usable=True,
    )


def _provider_cost_contract(item: dict) -> dict:
    if (
        item.get("provider_cost_ready") is not True
        or item.get("provider_cost_rectangle_count", 0) < 1
        or item.get("provider_cost_ready_rectangle_count")
        != item.get("provider_cost_rectangle_count")
    ):
        raise LabError("Relay provider cost coverage is not ready")
    rectangles = [
        rectangle
        for route in item.get("routes", [])
        for rectangle in route.get("provider_cost_rectangles", [])
    ]
    if len(rectangles) != item["provider_cost_rectangle_count"]:
        raise LabError("Relay provider cost rectangle inventory is incomplete")
    if any(
        rectangle.get("ready") is not True
        or not rectangle.get("contract_rate_id")
        or rectangle.get("rate_set_id") is not None
        or rectangle.get("billing_unit") != "output_second"
        or rectangle.get("currency") != "CNY"
        or type(rectangle.get("unit_amount_cents")) is not int
        or rectangle["unit_amount_cents"] <= 0
        or not rectangle.get("effective_from")
        for rectangle in rectangles
    ):
        raise LabError(
            "Local lab requires exact CNY output-second contract-rate evidence"
        )
    source_hashes = {
        rectangle.get("source_document_sha256") for rectangle in rectangles
    }
    if len(source_hashes) != 1 or not re.fullmatch(
        r"[0-9a-f]{64}", next(iter(source_hashes), "") or ""
    ):
        raise LabError("Provider cost rectangles do not share one immutable source")
    effective_times = [
        _utc(rectangle["effective_from"], "provider_cost_effective_at")
        for rectangle in rectangles
    ]
    return {
        "source_sha256": next(iter(source_hashes)),
        "effective_at": max(effective_times).isoformat().replace("+00:00", "Z"),
        "rate_micros": max(
            rectangle["unit_amount_cents"] * 10_000
            for rectangle in rectangles
        ),
    }


_COMMERCIAL_ROUTE_IDENTITY_FIELDS = (
    "route_id",
    "channel_id",
    "provider_name",
    "provider_account_id",
    "provider_key_index",
    "provider_key_fingerprint_prefix",
    "provider_credential_set_version",
    "route_binding_sha256",
    "upstream_model",
    "adapter_profile_id",
    "adapter_profile_revision",
)


def _commercial_route_snapshot(item: dict) -> tuple[dict, str]:
    try:
        routes = [
            {field: route[field] for field in _COMMERCIAL_ROUTE_IDENTITY_FIELDS}
            for route in item["routes"]
        ]
        routes.sort(key=lambda row: (row["route_id"], row["channel_id"]))
        snapshot = {
            "schema_version": 1,
            "public_model_id": item["relay_model_id"],
            "capability_revision": item["capability_revision"],
            "model_release_id": item["model_release_id"],
            "model_release_revision": item["model_release_revision"],
            "published_route_revision": item["published_route_revision"],
            "routing_release_sha256": item["routing_release_sha256"],
            "provider_cost_readiness_sha256": item[
                "provider_cost_readiness_sha256"
            ],
            "routes": routes,
        }
    except (KeyError, TypeError) as exc:
        raise LabError("Current route identity evidence is incomplete") from exc
    digest = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
    return snapshot, digest


def _commercial_plan_body(
    *,
    api: LabApi,
    audit: dict,
    item: dict,
    effective_capability: dict,
    override: dict,
) -> dict:
    cost = _provider_cost_contract(item)
    resolutions = {
        str(resolution).lower()
        for mode in effective_capability["modes"].values()
        for resolution in mode["limits"]["resolutions"]
    }
    if not resolutions or any(value not in _RESOLUTION_RANK for value in resolutions):
        raise LabError("Commercial capability resolution ceiling is not rankable")
    max_images = max(
        int(mode["limits"].get("max_images", 0))
        for mode in effective_capability["modes"].values()
    )
    if max_images < 1:
        raise LabError("Commercial static-shot scope lacks image inputs")
    price = api.manifest["budget"]["unit_price_points_per_second"]
    fx_source_material = (
        f"local-video-lab:cny-identity-v1:{api.manifest['lab_id']}"
    ).encode("utf-8")
    return {
        "expected_capability_version": item["platform_capability_version"],
        "expected_candidate_revision": item["candidate_revision"],
        "expected_catalog_revision": audit["catalog_revision"],
        "expected_routing_release_sha256": item["routing_release_sha256"],
        "provider_cost_currency": "CNY",
        "provider_cost_formula": {
            "schema_version": 1,
            "kind": "resolution_output_second",
            "platform_billing_unit": "per_second",
            "source_capability_revision": item["candidate_revision"],
            "assumptions": {
                "quantity_basis": "relay_effective_capability_ceiling",
                "personal_media_policy": "explicit_image_input_v1",
                "enforced_limits": {
                    "max_resolution": max(
                        resolutions, key=lambda value: _RESOLUTION_RANK[value]
                    ),
                    "max_reference_images": max_images,
                    "included_reference_images": max_images,
                },
            },
            "components": [
                {
                    "component": "output_second",
                    "rate_micros": cost["rate_micros"],
                    "quantity_numerator": 1,
                    "quantity_denominator": 1,
                }
            ],
        },
        "provider_cost_evidence_kind": "contract_rate",
        "provider_cost_evidence_reference": (
            "relay-controlled-cost-document:sha256:" + cost["source_sha256"]
        ),
        "provider_cost_evidence_sha256": cost["source_sha256"],
        "provider_cost_effective_at": cost["effective_at"],
        "fx_cny_micros_per_currency_unit": 1_000_000,
        "fx_source": "CNY identity rate",
        "fx_version": "cny-identity-v1",
        "fx_evidence_sha256": hashlib.sha256(fx_source_material).hexdigest(),
        "fx_effective_at": api.manifest["created_at_utc"],
        "personal_price_points": price,
        "enterprise_price_points": price,
        "personal_config_override": override,
        "enterprise_config_override": override,
        "approval_reason": (
            f"隔离本地视频联调 {api.manifest['lab_id']}：绑定完整路由、"
            "成本证据与显式个人图片范围；非生产发布"
        ),
        "idempotency_key": (
            f"lab-commercial-plan:{api.manifest['lab_id']}:"
            f"{item['relay_model_id']}"
        ),
    }


def _verify_commercial_plan_binding(
    *,
    api: LabApi,
    audit: dict,
    item: dict,
    effective_capability: dict,
    override: dict,
    plan: dict,
) -> None:
    expected = _commercial_plan_body(
        api=api,
        audit=audit,
        item=item,
        effective_capability=effective_capability,
        override=override,
    )
    direct_fields = {
        "candidate_revision": expected["expected_candidate_revision"],
        "candidate_catalog_revision": expected["expected_catalog_revision"],
        "capability_version": expected["expected_capability_version"],
        "provider_cost_currency": expected["provider_cost_currency"],
        "provider_cost_formula": expected["provider_cost_formula"],
        "provider_cost_evidence_kind": expected[
            "provider_cost_evidence_kind"
        ],
        "provider_cost_evidence_reference": expected[
            "provider_cost_evidence_reference"
        ],
        "provider_cost_evidence_sha256": expected[
            "provider_cost_evidence_sha256"
        ],
        "fx_cny_micros_per_currency_unit": expected[
            "fx_cny_micros_per_currency_unit"
        ],
        "fx_source": expected["fx_source"],
        "fx_version": expected["fx_version"],
        "fx_evidence_sha256": expected["fx_evidence_sha256"],
        "personal_price_points": expected["personal_price_points"],
        "enterprise_price_points": expected["enterprise_price_points"],
        "personal_config_override": expected["personal_config_override"],
        "enterprise_config_override": expected["enterprise_config_override"],
        "approval_reason": expected["approval_reason"],
        "idempotency_key": expected["idempotency_key"],
    }
    if any(plan.get(field) != value for field, value in direct_fields.items()):
        raise LabError("Released commercial plan differs from the exact lab intent")
    for field in ("provider_cost_effective_at", "fx_effective_at"):
        try:
            observed_time = datetime.fromisoformat(
                str(plan.get(field)).replace("Z", "+00:00")
            )
            expected_time = datetime.fromisoformat(
                str(expected[field]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise LabError("Released commercial evidence time is invalid") from exc
        if observed_time.tzinfo is None:
            observed_time = observed_time.replace(tzinfo=timezone.utc)
        if expected_time.tzinfo is None:
            expected_time = expected_time.replace(tzinfo=timezone.utc)
        observed_time = observed_time.astimezone(timezone.utc)
        expected_time = expected_time.astimezone(timezone.utc)
        if observed_time != expected_time:
            raise LabError("Released commercial evidence time differs from lab intent")
    route_snapshot, route_identity_sha256 = _commercial_route_snapshot(item)
    publication_receipt = plan.get("publication_receipt")
    if (
        plan.get("approved_route_identity") != route_snapshot
        or plan.get("approved_route_identity_sha256") != route_identity_sha256
        or plan.get("released_route_identity_sha256") != route_identity_sha256
        or not isinstance(publication_receipt, dict)
        or publication_receipt.get("route_identity_sha256")
        != route_identity_sha256
    ):
        raise LabError(
            "Current Relay account, key or route identity differs from the released plan"
        )


def verify_lab(api: LabApi, expected: dict[str, dict]) -> dict:
    identity = api.identity()
    if not all(
        identity.get(key)
        for key in (
            "company_id",
            "user_id",
            "admin_user_id",
            "personal_user_id",
            "personal_workspace_id",
        )
    ):
        raise LabError("Lab identity has not been provisioned")
    admin = identity["admin_user_id"]
    evidence = api.request(
        "GET", "/api/v1/platform-admin/relay-models", admin=admin
    )
    evidence_items = _select_lab_relay_items(evidence, expected)
    if (not evidence.get("route_evidence_available")
            or any(item.get("route_evidence_status") != "ready" or item.get("requires_approval")
                   for item in evidence_items.values())):
        raise LabError("Current route release evidence is not ready for lab verification")
    effective_by_slug: dict[str, dict] = {}
    override_by_slug: dict[str, dict] = {}
    for slug, item in evidence_items.items():
        capability = item.get("capabilities", {})
        if not _relay_capability_matches(
            capability,
            expected[slug],
            provider_mode=api.manifest["provider_mode"],
        ):
            raise LabError("Relay capability differs from the reviewed model contract")
        override = _commercial_override(capability)
        override_by_slug[slug] = override
        effective_by_slug[slug] = _effective_commercial_capability(
            capability, override
        )
        _provider_cost_contract(item)

    plans = api.request(
        "GET", "/api/v1/platform-admin/model-commercial-releases", admin=admin
    )
    released_plans: dict[str, dict] = {}
    for slug in expected:
        matches = [
            plan
            for plan in plans
            if plan.get("model_slug") == slug and plan.get("state") == "released"
        ]
        if len(matches) != 1:
            raise LabError("Lab model lacks one exact released commercial plan")
        plan = matches[0]
        _verify_commercial_plan_binding(
            api=api,
            audit=evidence,
            item=evidence_items[slug],
            effective_capability=effective_by_slug[slug],
            override=override_by_slug[slug],
            plan=plan,
        )
        released_plans[slug] = plan

    activation = api.request("GET", ACTIVATION_PATH, admin=admin)
    activation_receipt = activation.get("receipt")
    if (
        not isinstance(activation_receipt, dict)
        or activation_receipt.get("kind")
        != "local_video_lab_exact_commercial_activation_receipt"
        or activation_receipt.get("lab_id") != api.manifest["lab_id"]
        or activation_receipt.get("manifest_sha256")
        != manifest_sha256(api.manifest)
        or activation_receipt.get("test_data_only") is not True
        or activation_receipt.get("cash_basis_cents") != 0
        or activation_receipt.get("budget") != api.manifest["budget"]
        or activation_receipt.get("company_scope")
        != {
            "user_id": identity["user_id"],
            "company_id": identity["company_id"],
            "personal_workspace_id": None,
        }
        or activation_receipt.get("personal_scope")
        != {
            "user_id": identity["personal_user_id"],
            "company_id": None,
            "personal_workspace_id": identity["personal_workspace_id"],
        }
        or activation.get("receipt_sha256")
        != hashlib.sha256(
            _canonical(activation_receipt).encode("utf-8")
        ).hexdigest()
    ):
        raise LabError("Local lab activation receipt is invalid")
    activation_plans = {
        row.get("model_slug"): row
        for row in activation_receipt.get("plans", [])
        if isinstance(row, dict)
    }
    if set(activation_plans) != set(expected):
        raise LabError("Local lab activation receipt plan set is not exact")
    for slug, plan in released_plans.items():
        receipt_plan = activation_plans[slug]
        if (
            receipt_plan.get("plan_id") != plan["id"]
            or receipt_plan.get("plan_content_sha256")
            != plan["content_sha256"]
            or receipt_plan.get("route_identity_sha256")
            != plan["released_route_identity_sha256"]
            or receipt_plan.get("provider_cost_evidence_sha256")
            != plan["provider_cost_evidence_sha256"]
            or receipt_plan.get("publication_receipt_sha256")
            != plan["publication_receipt_sha256"]
            or receipt_plan.get("company_call_quota")
            != api.manifest["budget"]["call_quota"]
            or receipt_plan.get("company_concurrency_limit")
            != api.manifest["budget"]["concurrency_limit"]
            or receipt_plan.get("personal_call_quota")
            != api.manifest["budget"]["call_quota"]
            or receipt_plan.get("personal_concurrency_limit")
            != api.manifest["budget"]["concurrency_limit"]
        ):
            raise LabError("Local lab activation receipt differs from a released plan")

    company_principal = {
        "user_id": identity["user_id"],
        "company_id": identity["company_id"],
        "personal_workspace_id": None,
    }
    personal_principal = {
        "user_id": identity["personal_user_id"],
        "company_id": None,
        "personal_workspace_id": identity["personal_workspace_id"],
    }
    available = api.request(
        "GET",
        f"/api/v1/companies/{identity['company_id']}/models",
        identity=company_principal,
    )
    by_slug = {model["slug"]: model for model in available}
    if set(by_slug) != set(expected):
        raise LabError("Customer Platform discovery is not the exact eligible lab model set")
    personal_available = api.request(
        "GET", "/api/v1/personal/models", identity=personal_principal
    )
    personal_by_slug = {
        row["slug"]: row for row in personal_available
    }
    if set(personal_by_slug) != set(expected):
        raise LabError("Personal Platform discovery is not the exact eligible lab model set")
    models = []
    company_acceptance_models = []
    personal_acceptance_models = []
    for slug, item in sorted(by_slug.items()):
        personal = personal_by_slug[slug]
        effective = effective_by_slug[slug]
        if (item["effective_capabilities"] != effective
            or personal.get("effective_capabilities") != effective
            or personal.get("billing_unit") != "POINT"
            or personal.get("billing_version") != 2
            or item.get("relay_capability_revision") != evidence_items[slug]["capability_revision"]):
            raise LabError("Customer discovery differs from the released commercial restriction")
        if (
            not item.get("quote_revision")
            or item.get("billing_unit") != "POINT"
            or item.get("billing_version") != 2
            or item.get("unit_price_points")
            != api.manifest["budget"]["unit_price_points_per_second"]
        ):
            raise LabError("Lab discovery lacks a versioned point quote")
        if (
            not personal.get("quote_revision")
            or personal.get("unit_price_points")
            != api.manifest["budget"]["unit_price_points_per_second"]
            or personal.get("call_quota")
            != api.manifest["budget"]["call_quota"]
            or personal.get("concurrency_limit")
            != api.manifest["budget"]["concurrency_limit"]
        ):
            raise LabError(
                "Personal discovery lacks the released quote or usage policy"
            )
        modes = item["effective_capabilities"]["modes"]
        t2v = modes.get("text_to_video")
        i2v = modes.get("image_to_video")
        readiness = item.get("mode_readiness", {}).get("text_to_video", {})
        if (
            not t2v
            or not i2v
            or not readiness.get("default", {}).get("ready")
            or i2v.get("input_media_types") != ["image"]
            or i2v["limits"].get("max_images", 0) < 1
            or i2v["limits"].get("max_videos") != 0
            or i2v["limits"].get("max_audio") != 0
        ):
            raise LabError("Lab static image-to-video discovery is not ready")
        limits = t2v["limits"]
        request_payload = {
            "mode": "text_to_video",
            "prompt": "本地视频链路验证，清晨的海面与缓慢移动的镜头。",
            "duration_seconds": min(limits["duration_seconds"]),
            "resolution": limits["resolutions"][0],
            "aspect_ratio": limits["aspect_ratios"][0],
            "output_count": 1,
            "face_enabled": False,
        }
        company_acceptance = {
            "id": item["id"],
            "slug": slug,
            "status": "ready",
            "expected_capability_version": item["capability_version"],
            "expected_quote_revision": item["quote_revision"],
            "expected_relay_capability_revision": item[
                "relay_capability_revision"
            ],
            "commercial_release_plan_id": released_plans[slug]["id"],
            "effective_capabilities": item["effective_capabilities"],
            "mode_readiness": item["mode_readiness"],
            "billing_unit": item["billing_unit"],
            "billing_version": item["billing_version"],
            "unit_price_points": item["unit_price_points"],
            "request_payload": request_payload,
        }
        personal_acceptance = {
            **company_acceptance,
            "expected_capability_version": personal["capability_version"],
            "expected_quote_revision": personal["quote_revision"],
            "effective_capabilities": personal["effective_capabilities"],
            "mode_readiness": personal["mode_readiness"],
            "billing_unit": personal["billing_unit"],
            "billing_version": personal["billing_version"],
            "unit_price_points": personal["unit_price_points"],
            "call_quota": personal["call_quota"],
            "concurrency_limit": personal["concurrency_limit"],
        }
        company_acceptance_models.append(company_acceptance)
        personal_acceptance_models.append(personal_acceptance)
        # Retain the original company-shaped list for old lab readers while
        # making the separate personal quote explicit. New acceptance code
        # must select one exact acceptance_scopes entry and cannot mix them.
        models.append({
            **company_acceptance,
            "personal_expected_quote_revision": personal["quote_revision"],
        })
    return {"schema_version": 1, "kind": KIND, "lab_id": api.manifest["lab_id"],
            "provider_mode": api.manifest["provider_mode"], "manifest_sha256": manifest_sha256(api.manifest),
            "test_data_only": True, "real_provider_acceptance": False,
            "provider_tasks_submitted_by_cli": 0, "company_id": identity["company_id"],
            "user_id": identity["user_id"], "admin_user_id": identity["admin_user_id"],
            "personal_user_id": identity["personal_user_id"],
            "personal_workspace_id": identity["personal_workspace_id"],
            "commercial_activation": activation,
            "default_acceptance_scope_kind": "personal",
            "acceptance_scopes": {
                "company": {
                    "scope_kind": "company",
                    "user_id": identity["user_id"],
                    "company_id": identity["company_id"],
                    "personal_workspace_id": None,
                    "models": company_acceptance_models,
                },
                "personal": {
                    "scope_kind": "personal",
                    "user_id": identity["personal_user_id"],
                    "company_id": None,
                    "personal_workspace_id": identity["personal_workspace_id"],
                    "models": personal_acceptance_models,
                },
            },
            "models": models}


def provision_lab(api: LabApi, expected: dict[str, dict]) -> dict:
    _require_live_approval(api.manifest)
    identity = api.identity()  # Mandatory before the first mutation.
    names = _names(api.manifest)
    if not identity.get("admin_user_id"):
        api.request("POST", "/api/v1/bootstrap/platform-admin",
                    {"email": names["admin_email"], "display_name": "本地视频联调管理员"})
        identity = api.identity()
    if not identity.get("company_id"):
        api.request("POST", "/api/v1/bootstrap", {key: names[key] for key in ("company_name", "owner_email", "owner_display_name")})
        identity = api.identity()
    admin = identity["admin_user_id"]
    if not identity.get("personal_user_id"):
        api.request("POST", PERSONAL_IDENTITY_PATH, {}, admin=admin)
        identity = api.identity()
    audit = api.request("POST", "/api/v1/platform-admin/relay-models/reconcile", admin=admin)
    items = _select_lab_relay_items(audit, expected)
    if not audit.get("route_evidence_available") or any(item.get("route_evidence_status") != "ready" for item in items.values()):
        raise LabError("Current Relay route acceptance/fresh probe evidence is missing; drafts remain unpublished")
    commercial: dict[str, tuple[dict, dict]] = {}
    for slug, item in sorted(items.items()):
        capability = item.get("capabilities", {})
        if not _relay_capability_matches(
            capability,
            expected[slug],
            provider_mode=api.manifest["provider_mode"],
        ):
            raise LabError("Relay capability differs from the reviewed model contract")
        if not item.get("platform_model_id") or item.get("platform_capability_version") is None:
            raise LabError("Relay reconciliation did not materialize a Platform candidate")
        override = _commercial_override(capability)
        effective = _effective_commercial_capability(capability, override)
        _provider_cost_contract(item)
        commercial[slug] = (override, effective)

    if identity["billing_version"] == 1:
        api.request("POST", f"/api/v1/platform-admin/companies/{identity['company_id']}/billing/migrate-to-points", {
            "expected_available_cents": 0, "idempotency_key": f"lab-points-migration:{api.manifest['lab_id']}",
        }, admin=admin)
    activation_plans: list[dict[str, str]] = []
    for slug, item in sorted(items.items()):
        override, effective = commercial[slug]
        approved = api.request(
            "PUT",
            f"/api/v1/platform-admin/models/{item['platform_model_id']}"
            "/commercial-release-plan",
            _commercial_plan_body(
                api=api,
                audit=audit,
                item=item,
                effective_capability=effective,
                override=override,
            ),
            admin=admin,
        )
        if approved.get("state") not in {"approved", "released"}:
            raise LabError("Commercial plan approval did not reach a releasable state")
        activation_plans.append({
            "plan_id": approved["id"],
            "model_id": approved["model_id"],
            "model_slug": approved["model_slug"],
            "plan_content_sha256": approved["content_sha256"],
        })
    api.request(
        "POST",
        ACTIVATION_PATH,
        {"plans": activation_plans},
        admin=admin,
    )
    return verify_lab(api, expected)


@contextmanager
def apply_lock(manifest_path: Path):
    path = manifest_path.with_name(manifest_path.name + ".platform-apply.lock")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise LabError("A lab apply lock already exists; reconcile its owner before retrying") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--verify", action="store_true")
    actions.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        expected = allowed_model_contracts()
        if not args.apply and not args.verify:
            result = {"kind": KIND, "lab_id": manifest["lab_id"], "preview_only": True,
                      "provider_mode": manifest["provider_mode"], "test_data_only": True,
                      "targets": manifest["targets"], "eligible_model_ids": sorted(expected),
                      "will_submit_provider_tasks": False, "requires_route_evidence": True}
        else:
            _require_live_approval(manifest)
            api = LabApi(manifest, os.environ.get(TOKEN_ENV, ""))
            try:
                if args.apply:
                    with apply_lock(args.manifest):
                        result = provision_lab(api, expected)
                else:
                    result = verify_lab(api, expected)
            finally:
                api.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (LabError, OSError) as exc:
        message = str(exc) if isinstance(exc, LabError) else "Cannot access local lab files"
        print(json.dumps({"kind": KIND, "status": "blocked", "reason": message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
