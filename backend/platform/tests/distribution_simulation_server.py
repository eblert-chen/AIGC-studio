"""Loopback-only, disposable real-Platform distribution rehearsal.

Run from backend/platform with:
  python -m uvicorn tests.distribution_simulation_server:create_simulation_app \
    --factory --host 127.0.0.1 --port 18220

No production factory imports this file. Identity bootstrapping and fault controls
are simulation-only. All model/cost/approval/grant writes use production HTTP
handlers; Relay bytes come from the sibling Go lifecycle's atomic export.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, func, select

from platform_api.auth import OidcIdTokenClaims
from platform_api.config import Settings
from platform_api.dependencies import get_db
from platform_api.main import create_app
from platform_api.models import (
    Company, CompanyModelGrant, ExternalIdentity, GenerationTask,
    ModelCommercialReleasePlan, ModelDefinition, PersonalRetailModelGrant,
    RelaySubmissionOutbox, User,
)
from platform_api.routers.authentication import _set_session_cookies
from platform_api.services.authentication import SessionService
from platform_api.services.audit import AuditService
from platform_api.services.personal import PersonalWorkspaceService

ROOT = Path(__file__).resolve().parents[3]
RUN = os.environ.get("MODEL_DISTRIBUTION_SIMULATION_RUN", "20260908")
if re.fullmatch(r"[0-9]{8}(?:-v[1-9][0-9]{0,2})?", RUN) is None:
    raise RuntimeError("Simulation run must be eight ASCII digits with an optional explicit -v1..-v999 suffix")
EVIDENCE = ROOT / f"artifacts/model-distribution-simulation-{RUN}"
PLATFORM_DIR = EVIDENCE / "platform"
RELAY_SUBDIR = os.environ.get("MODEL_DISTRIBUTION_SIMULATION_RELAY_SUBDIR", "relay")
if RELAY_SUBDIR not in {"relay", "relay-final"}:
    raise RuntimeError("Invalid isolated Relay attempt namespace")
RELAY_DIR = EVIDENCE / RELAY_SUBDIR
API_ORIGIN = "http://127.0.0.1:18220"
FRONTEND_ORIGIN = "http://127.0.0.1:14278"
INSTANCE_ID = f"distribution-simulation-{RUN}"
ISSUER = f"urn:ai-video:distribution-simulation:{RUN}"
BOOTSTRAP = "simulation-bootstrap-only-20260908-no-production-authority"
ADMISSION = "simulation-relay-export-only-20260908"
_trace_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _redact(value):
    if isinstance(value, dict):
        return {
            key: ("[redacted]" if any(word in key.lower() for word in
                  ("token", "secret", "password", "cookie")) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _trace(value):
    with _trace_lock:
        with (PLATFORM_DIR / "http-trace.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(_redact(value), ensure_ascii=False) + "\n")


class TraceMiddleware:
    """Capture actual handler inputs/outputs, excluding credentials and cookies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/api/", "/__simulation__/")):
            return await self.app(scope, receive, send)
        incoming = bytearray()
        outgoing = bytearray()
        status = 500
        request_id = None

        async def traced_receive():
            message = await receive()
            if message["type"] == "http.request":
                incoming.extend(message.get("body", b""))
            return message

        async def traced_send(message):
            nonlocal status, request_id
            if message["type"] == "http.response.start":
                status = message["status"]
                request_id = next((v.decode() for k, v in message["headers"]
                                   if k.lower() == b"x-request-id"), None)
            elif message["type"] == "http.response.body":
                outgoing.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, traced_receive, traced_send)
        finally:
            def payload(data):
                try:
                    return json.loads(data) if data else None
                except (ValueError, UnicodeError):
                    return "[non-JSON omitted]"
            _trace({"at": _now(), "simulation": True, "method": scope["method"],
                    "path": scope["path"], "query": scope.get("query_string", b"").decode(),
                    "request_id": request_id, "request": payload(incoming),
                    "status": status, "response": payload(outgoing)})


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: Literal["owner", "personal", "company", "non_owner"]


class PhaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["before", "prepared", "published", "expired", "unavailable"]


def create_simulation_app():
    PLATFORM_DIR.mkdir(parents=True, exist_ok=True)
    db_path = PLATFORM_DIR / "simulation.sqlite3"
    engine = create_engine("sqlite+pysqlite:///" + db_path.as_posix(),
                           connect_args={"check_same_thread": False, "timeout": 20})
    settings = Settings(
        _env_file=None, environment="test", app_name="SIMULATION ONLY · Real Platform distribution",
        database_url="sqlite+pysqlite:///" + db_path.as_posix(), auto_create_tables=True,
        development_header_auth_enabled=True, enable_bootstrap=True, bootstrap_token=BOOTSTRAP,
        relay_backends={"new-api-v1": {"base_url": API_ORIGIN,
                        "client_id": "distribution-simulation", "api_key": ADMISSION,
                        "internal_admission_token": ADMISSION}},
        relay_legacy_compatibility_enabled=False,
        internal_service_token="simulation-internal-only-no-worker",
        jwt_signing_secret="simulation-session-pepper-never-valid-in-production-20260908",
        download_edge_completion_service_token="simulation-edge-no-service",
        channel_cost_signing_secret="simulation-channel-cost-no-callbacks",
        relay_telemetry_signing_secret="simulation-relay-telemetry-no-callbacks",
        download_completion_edge_gateway_signing_secret="simulation-edge-no-gateway",
        download_completion_obs_access_log_signing_secret="simulation-obs-no-storage",
        download_gateway_attempt_encryption_key_base64="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        input_asset_filesystem_root=str(PLATFORM_DIR / "input-assets"),
        input_asset_public_base_url=API_ORIGIN, input_asset_relay_base_url=API_ORIGIN,
        input_asset_signing_secret="simulation-input-no-supplier-calls",
    )
    # Like local-video-lab: a local session bootstrap is not an OIDC provider.
    # Bind exact issuer/sub and CSRF origin only in this disposable app copy.
    settings = settings.model_copy(update={"frontend_origin": FRONTEND_ORIGIN,
                                           "oidc_issuer": ISSUER,
                                           "platform_owner_user_ids": ["owner"]})
    app = create_app(settings=settings, engine=engine)
    app.add_middleware(TraceMiddleware)
    app.state.simulation_fault = None
    manifest_path = PLATFORM_DIR / "identity.json"
    if manifest_path.exists():
        identities = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        # Real bootstrap handlers create only identities and an empty CNY company.
        # Do not enter a nested ASGI lifespan: its shutdown would close the real
        # Relay HTTP client before uvicorn starts serving this application.
        client = TestClient(app, headers={"X-Bootstrap-Token": BOOTSTRAP})
        try:
            owner = client.post("/api/v1/bootstrap/platform-admin", json={
                "email": "simulation-owner@example.com", "display_name": "模拟平台所有者"})
            non_owner = client.post("/api/v1/bootstrap/platform-admin", json={
                "email": "simulation-non-owner@example.com", "display_name": "模拟未授权管理员"})
            company = client.post("/api/v1/bootstrap", json={
                "company_name": "模拟分发验收企业（独立数据库）",
                "owner_email": "simulation-company@example.com",
                "owner_display_name": "模拟企业所有者"})
            for result in (owner, non_owner, company):
                if result.status_code != 201:
                    raise RuntimeError("Simulation identity bootstrap failed: " + result.text)
        finally:
            client.close()
        identities = {"owner": owner.json()["user_id"],
                      "non_owner": non_owner.json()["user_id"],
                      "company": company.json()["user_id"],
                      "company_id": company.json()["company_id"]}
        with app.state.session_factory.begin() as session:
            personal = User(email="simulation-personal@example.com", display_name="模拟个人创作者")
            session.add(personal)
            session.flush()
            workspace = PersonalWorkspaceService.ensure(session, user_id=personal.id)
            identities["personal"] = personal.id
            identities["personal_workspace_id"] = workspace.id
        manifest_path.write_text(json.dumps(identities, indent=2, ensure_ascii=False), encoding="utf-8")

    def require_local_control(request: Request):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "Simulation controls are loopback only")
        if request.headers.get("origin") not in {None, FRONTEND_ORIGIN}:
            raise HTTPException(403, "Simulation origin mismatch")

    def bundle():
        try:
            value = json.loads((RELAY_DIR / "bundle.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(503, "Waiting for real isolated Relay handler export") from None
        if value.get("phase") not in {"before", "prepared", "published"}:
            raise HTTPException(503, "Relay export phase is not recognized")
        return value

    @app.get("/__simulation__/config", dependencies=[Depends(require_local_control)])
    def config():
        return {"kind": "ai-video-model-distribution-simulation", "simulation_id": INSTANCE_ID,
                "isolated": True, "paid_provider_calls": False, "company_id": identities["company_id"],
                "simulation": True, "test_data_only": True, "instance_id": INSTANCE_ID,
                "api_origin": API_ORIGIN, "frontend_origin": FRONTEND_ORIGIN,
                "identity": identities, "login_path": "/__simulation__/login",
                "relay_export": str(RELAY_DIR / "bundle.json"),
                "boundaries": ["No paid supplier", "No payment provider", "No production database",
                               "Synthetic Relay provider transport and cost evidence", "Real Platform handlers"]}

    @app.post("/__simulation__/login", dependencies=[Depends(require_local_control)])
    def login(body: LoginBody, request: Request, session=Depends(get_db, scope="function")):
        user = session.get(User, identities[body.persona])
        if user is None:
            raise HTTPException(409, "Simulation identity was removed")
        external = session.scalar(select(ExternalIdentity).where(
            ExternalIdentity.issuer == ISSUER, ExternalIdentity.subject == body.persona))
        now = datetime.now(timezone.utc)
        if external is None:
            external = ExternalIdentity(user_id=user.id, issuer=ISSUER, subject=body.persona,
                                        email_at_link=user.email, last_login_at=now)
            session.add(external)
            session.flush()
        if external.user_id != user.id:
            raise HTTPException(409, "Simulation identity binding changed")
        claims = OidcIdTokenClaims(issuer=ISSUER, subject=body.persona, email=user.email,
                                  display_name=user.display_name, issued_at=now.timestamp(),
                                  expires_at=now.timestamp() + 14400,
                                  authentication_time=now.timestamp(),
                                  authentication_methods=("isolated_simulation_bootstrap",))
        auth_session, raw_session, raw_csrf = SessionService.create(
            session, user=user, identity=external, claims=claims, ttl_seconds=14400,
            user_agent="isolated-distribution-simulation", pepper=settings.jwt_signing_secret,
            ip_hash=None, request_id=request.state.request_id)
        AuditService.append(session, actor_user_id=user.id, action="simulation.identity.login",
                            target_type="auth_session", target_id=auth_session.id,
                            before_summary={}, after_summary={"simulation": True, "persona": body.persona},
                            request_id=request.state.request_id)
        response = JSONResponse({"simulation": True, "instance_id": INSTANCE_ID,
                                 "persona": body.persona, "user_id": user.id,
                                 "company_id": identities["company_id"] if body.persona == "company" else None},
                                headers={"Cache-Control": "no-store"})
        _set_session_cookies(response, raw_session=raw_session, raw_csrf=raw_csrf, max_age=14400)
        return response

    @app.get("/__simulation__/state", dependencies=[Depends(require_local_control)])
    def state(session=Depends(get_db, scope="function")):
        try:
            current = bundle()
            relay = {"phase": current["phase"], "exported_at": current.get("exported_at"),
                     "catalog_revision": current["models"].get("catalog_revision")}
        except HTTPException:
            relay = {"phase": "waiting"}
        company = session.get(Company, identities["company_id"])
        return {"simulation": True, "instance_id": INSTANCE_ID, "relay": relay,
                "fault": app.state.simulation_fault, "company_billing_version": company.billing_version,
                "counts": {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                           for model in (ModelDefinition, ModelCommercialReleasePlan, CompanyModelGrant,
                                         PersonalRetailModelGrant, GenerationTask, RelaySubmissionOutbox)}}

    @app.post("/__simulation__/relay-phase", dependencies=[Depends(require_local_control)])
    def phase(body: PhaseBody):
        if body.phase in {"expired", "unavailable"}:
            app.state.simulation_fault = body.phase
        else:
            RELAY_DIR.mkdir(parents=True, exist_ok=True)
            (RELAY_DIR / "phase.txt").write_text(body.phase, encoding="utf-8")
            app.state.simulation_fault = None
        return {"simulation": True, "requested_phase": body.phase,
                "fault_injection": app.state.simulation_fault,
                "note": "Real lifecycle phase changes complete asynchronously; inspect state/export."}

    def exported_document(name):
        if app.state.simulation_fault == "unavailable":
            raise HTTPException(503, "Explicit simulation: Relay evidence unavailable")
        current = bundle()
        document = current[name]
        if app.state.simulation_fault == "expired" and name == "release_evidence":
            document = copy.deepcopy(document)
            document["generated_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        return document

    @app.get("/v1/models")
    def relay_catalog(request: Request):
        if request.headers.get("x-api-key") != ADMISSION:
            raise HTTPException(403, "Simulation export admission required")
        document = exported_document("models")
        return JSONResponse(document, headers={"ETag": '"' + document["catalog_revision"] + '"',
                                               "Cache-Control": "no-store"})

    @app.get("/internal/platform-relay/model-release-evidence")
    def relay_evidence(request: Request):
        if request.headers.get("x-relay-internal-admission") != ADMISSION:
            raise HTTPException(403, "Simulation export admission required")
        return JSONResponse(exported_document("release_evidence"), headers={"Cache-Control": "no-store"})

    return app
