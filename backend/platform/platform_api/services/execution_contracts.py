from __future__ import annotations

import hmac
import json
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..execution_contract import ExecutionContract
from ..relay_client import RelayModelReleaseEvidenceItem
from ..models import RelaySubmissionOutbox
from .errors import ConflictError


def freeze_execution_contract(
    *, expected_snapshot: Mapping[str, Any] | None, request_payload: Mapping[str, Any],
) -> ExecutionContract:
    """Use only the release evidence already checked under the model lock."""
    # 开发环境快捷路径：当没有 expected_snapshot 时，使用假数据跳过精确路由和成本版本检查。
    # 契约路由必须与本次请求的 mode/resolution 一致，否则
    # RelayGenerationRequest.validate_execution_rectangle 会拒绝。
    import os
    if os.environ.get("ENVIRONMENT") == "development" and not expected_snapshot:
        dev_mode = request_payload.get("mode", "text_to_video")
        dev_resolution = request_payload.get("resolution", "720p")
        return ExecutionContract.model_validate({
            "schema_version": 1,
            "routing_release_sha256": "sha256:" + "0" * 64,
            "provider_cost_readiness_sha256": "sha256:" + "0" * 64,
            "routes": [{
                "route_id": "dev-route-1",
                "channel_id": 1,
                "provider_account_id": "dev-account",
                "provider_credential_set_version": "dev-version",
                "route_binding_sha256": "sha256:" + "0" * 64,
                "mode": dev_mode,
                "resolution": dev_resolution,
                "cost_kind": "contract_rate",
                "cost_id": "00000000-0000-0000-0000-000000000001",
                "cost_sha256": "sha256:" + "0" * 64,
            }],
        })
    try:
        evidence = RelayModelReleaseEvidenceItem.model_validate_json(
            json.dumps((expected_snapshot or {}).get("route_release_evidence"))
        )
        if evidence.status != "ready" or not evidence.provider_cost_ready:
            raise ValueError("release is not ready")
        mode = request_payload.get("mode", "text_to_video")
        resolution = request_payload.get("resolution", "720p")
        routes = []
        for route in evidence.routes:
            if not (route.enabled and route.accepted and route.fresh):
                raise ValueError("route is not accepted")
            rectangles = [item for item in route.provider_cost_rectangles
                          if item.mode == mode and item.resolution == resolution and item.ready]
            if len(rectangles) != 1 or not rectangles[0].cost_revision_sha256:
                raise ValueError("exact cost revision is unavailable")
            cost = rectangles[0]
            routes.append({
                "route_id": route.route_id, "channel_id": route.channel_id,
                "provider_account_id": route.provider_account_id,
                "provider_credential_set_version": route.provider_credential_set_version,
                "route_binding_sha256": route.route_binding_sha256,
                "mode": mode, "resolution": resolution,
                "cost_kind": "rate_set" if cost.rate_set_id else "contract_rate",
                "cost_id": cost.rate_set_id or cost.contract_rate_id,
                "cost_sha256": cost.cost_revision_sha256,
            })
        return ExecutionContract.model_validate({
            "schema_version": 1,
            "routing_release_sha256": evidence.routing_release_sha256,
            "provider_cost_readiness_sha256": evidence.provider_cost_readiness_sha256,
            "routes": sorted(routes, key=lambda row: row["route_id"]),
        })
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        import os
        if os.environ.get("ENVIRONMENT") == "development":
            raise ConflictError(f"执行合同构造失败（开发环境调试）: {type(exc).__name__}: {exc}") from exc
        raise ConflictError("执行合同缺少精确路由或成本版本，请重新同步并审批模型") from exc


def require_execution_digest(task: Any, digest: str | None) -> None:
    expected = (task.pricing_snapshot or {}).get("execution_contract_sha256")
    if expected is None and digest is None:
        return  # Do not manufacture new authority for an historical request.
    if not isinstance(expected, str) or not isinstance(digest, str) or not hmac.compare_digest(expected, digest):
        raise ConflictError("Relay 执行合同与任务冻结版本不一致，保留预占并等待对账")


def require_execution_cost(
    session: Session, *, task: Any, identity: Mapping[str, Any],
    execution_digest: str | None, cost_digest: str | None,
) -> None:
    require_execution_digest(task, execution_digest)
    if execution_digest is None:
        if cost_digest is not None:
            raise ConflictError("历史任务不能补造执行成本版本")
        return
    outbox = session.scalar(select(RelaySubmissionOutbox).where(
        RelaySubmissionOutbox.task_id == task.id,
    ))
    try:
        contract = ExecutionContract.model_validate((outbox.relay_payload or {}).get("execution_contract"))
    except (ValidationError, AttributeError) as exc:
        raise ConflictError("任务缺少原始执行合同，供应商成本暂不入账") from exc
    require_execution_digest(task, contract.content_sha256())
    if contract.routing_release_sha256 != identity.get("routing_release_sha256"):
        raise ConflictError("供应商成本的路由发布版本与执行合同不一致")
    routes = [route for route in contract.routes if (
        route.route_id == identity.get("route_key")
        and route.channel_id == identity.get("provider_channel_id")
        and route.provider_account_id == identity.get("provider_account_id")
        and route.provider_credential_set_version == identity.get("provider_credential_version")
    )]
    if len(routes) != 1 or routes[0].cost_sha256 != cost_digest:
        raise ConflictError("供应商成本或账号不属于任务批准的执行合同")
