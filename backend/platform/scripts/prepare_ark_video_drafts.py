#!/usr/bin/env python3
"""Preview Platform draft requests from the single reviewed Relay manifest.

This offline helper reads no runtime configuration, opens no database or HTTP
connection, and writes only JSON to stdout. It does not import these drafts.
Normal onboarding remains verified Relay routes -> /v1/models -> the Platform
catalog-sync worker -> explicit approval, publication, pricing and distribution.
The manifest is adapter-support evidence, never live route-readiness evidence.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLATFORM_ROOT.parent.parent
DEFAULT_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "backend/new-api-relay/generationprofile/seedance_models.v1.json"
)

# Allow direct execution from any working directory without loading settings or
# the application. Imports below are schemas and pure contract validation only.
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_api.relay_client import RelayGenerationCapabilities  # noqa: E402
from platform_api.schemas import AdminModelCreateRequest  # noqa: E402
from platform_api.services.errors import ConflictError  # noqa: E402
from platform_api.services.task_admission import TaskCapabilityAdmission  # noqa: E402


def canonical_utc_datetime(value: str) -> datetime:
    """Parse an explicit review/lifecycle instant without local-time guessing."""

    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected canonical UTC RFC3339, such as 2026-08-31T00:00:00Z") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("Expected canonical UTC RFC3339")
    return parsed.replace(tzinfo=timezone.utc)


def prepare_draft_preview(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return review-only requests; never materialize or release a model."""

    instant = datetime.now(timezone.utc) if as_of is None else as_of
    if not isinstance(instant, datetime) or instant.utcoffset() is None:
        raise ValueError("The preview as_of instant must be timezone-aware")
    instant = instant.astimezone(timezone.utc)
    manifest_bytes = manifest_path.read_bytes()
    document = json.loads(manifest_bytes)
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or not isinstance(document.get("models"), list)
    ):
        raise ValueError("The reviewed Ark manifest must use schema_version 1")
    reviewed_at = document.get("reviewed_at")
    if (
        not isinstance(reviewed_at, str)
        or date.fromisoformat(reviewed_at).isoformat() != reviewed_at
    ):
        raise ValueError("The reviewed Ark manifest needs an ISO review date")

    models: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_provider_ids: set[str] = set()
    seen_public_ids: set[str] = set()
    for entry in document["models"]:
        if not isinstance(entry, dict):
            raise ValueError("Ark manifest model entries must be objects")
        lifecycle = entry.get("lifecycle")
        if lifecycle in {"retired", "unverified"}:
            continue
        if lifecycle not in {"acceptance_candidate", "deprecated"}:
            raise ValueError("Unknown Ark model lifecycle; review the manifest first")
        provider_model_id = entry.get("provider_model_id")
        public_model_id = entry.get("public_model_id")
        if not isinstance(provider_model_id, str) or not provider_model_id:
            raise ValueError("Ark model provider identity is missing")
        if not isinstance(public_model_id, str) or not public_model_id:
            raise ValueError("Ark model public identity is missing")
        if provider_model_id in seen_provider_ids or public_model_id in seen_public_ids:
            raise ValueError("Ark manifest model identities must be unique")
        seen_provider_ids.add(provider_model_id)
        seen_public_ids.add(public_model_id)
        new_routes_allowed = entry.get("new_routes_allowed")
        if not isinstance(new_routes_allowed, bool) or (
            lifecycle == "deprecated" and new_routes_allowed
        ):
            raise ValueError("Ark model route-onboarding policy is inconsistent")
        eos_at = entry.get("eos_at")
        if eos_at and instant >= canonical_utc_datetime(eos_at):
            excluded.append(
                {
                    "provider_model_id": provider_model_id,
                    "public_model_id": public_model_id,
                    "display_name": entry.get("display_name"),
                    "lifecycle": lifecycle,
                    "eom_at": entry.get("eom_at"),
                    "eos_at": eos_at,
                    "official_sources": entry.get("official_sources", []),
                    "archive_only": True,
                    "excluded_reason": "end_of_service",
                    "new_routes_allowed": False,
                    "route_policy": "archive_only",
                    "warning": "已达到停止服务时间；仅保留历史说明，不输出模型草稿请求。",
                }
            )
            continue

        capability = RelayGenerationCapabilities.model_validate(
            entry.get("capability")
        ).contract_dump()
        TaskCapabilityAdmission.validate_catalog(
            {"generation": capability}, require_usable=True
        )
        if any(
            mode["limits"]["output_counts"] != [1]
            for mode in capability["modes"].values()
        ):
            raise ValueError("Ark per-second draft requests require one output")
        request = AdminModelCreateRequest.model_validate(
            {
                "slug": public_model_id,
                "display_name": entry.get("display_name"),
                "provider_key": "relay",
                "billing_mode": "per_second",
                "capabilities": [{"key": "generation", "config": capability}],
            }
        )
        models.append(
            {
                "provider_model_id": provider_model_id,
                "public_model_id": public_model_id,
                "adapter_profile_id": entry.get("adapter_profile_id"),
                "lifecycle": lifecycle,
                "evidence_status": entry.get("evidence_status"),
                "official_sources": entry.get("official_sources", []),
                "eom_at": entry.get("eom_at"),
                "eos_at": entry.get("eos_at"),
                "new_routes_allowed": new_routes_allowed,
                "route_policy": (
                    "acceptance_required"
                    if new_routes_allowed
                    else "existing_routes_only"
                ),
                "warning": (
                    "代码合同支持不等于已启用或已验收的真实路由。"
                    if new_routes_allowed
                    else "仅供已有服务资格的客户与既有路由复核；不得默认新建路由。"
                ),
                "admin_create_request": request.model_dump(mode="json"),
            }
        )
    if not models and not excluded:
        raise ValueError("The reviewed Ark manifest has no current draft candidates")

    return {
        "schema_version": 1,
        "preview_only": True,
        "source_manifest": (
            manifest_path.relative_to(REPOSITORY_ROOT).as_posix()
            if manifest_path.is_relative_to(REPOSITORY_ROOT)
            else manifest_path.name
        ),
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "reviewed_at": reviewed_at,
        "as_of": instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "route_readiness_verified": False,
        "automatic_approval": False,
        "automatic_publish": False,
        "automatic_pricing": False,
        "automatic_distribution": False,
        "notice": (
            "离线声明预览，不是实时 Relay 目录，也不创建模型。生产入目录仍由已验收"
            " Relay /v1/models 的后台对账形成待审草稿；审批、发布、定价、分发均需显式执行。"
            "显式 as_of 仅用于历史只读审阅，不改变当前生命周期或任何真实审计时间。"
        ),
        "models": sorted(models, key=lambda model: model["public_model_id"]),
        "excluded": sorted(excluded, key=lambda model: model["public_model_id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        help=(
            "canonical UTC RFC3339 instant for historical read-only review; "
            "defaults to the current UTC clock and never applies changes"
        ),
    )
    args = parser.parse_args()
    try:
        preview = prepare_draft_preview(
            as_of=canonical_utc_datetime(args.as_of) if args.as_of is not None else None
        )
    except (OSError, ValueError, ConflictError) as exc:
        parser.exit(2, f"Ark draft preview failed: {exc}\n")
    # ASCII escaping keeps the JSON lossless on Windows consoles regardless of
    # their code page. This is stdout-only; there is deliberately no apply flag.
    print(json.dumps(preview, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
