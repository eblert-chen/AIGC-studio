#!/usr/bin/env python3
"""Preview MiniMax H3 Platform drafts; never import, approve or release them.

The reviewed Relay manifest is the single capability source. This helper opens
no database or HTTP connection and writes only JSON to stdout. Account access,
paid route acceptance, customer prices and workspace grants remain unverified.
Normal onboarding still uses verified Relay routes -> /v1/models -> unpublished
Platform candidates -> explicit approval, publication, pricing and distribution.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLATFORM_ROOT.parent.parent
DEFAULT_MANIFEST_PATH = (
    REPOSITORY_ROOT / "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json"
)

if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_api.relay_client import RelayGenerationCapabilities  # noqa: E402
from platform_api.schemas import AdminModelCreateRequest  # noqa: E402
from platform_api.services.errors import ConflictError  # noqa: E402
from platform_api.services.task_admission import TaskCapabilityAdmission  # noqa: E402


def prepare_draft_preview(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    """Build review-only API request bodies without runtime settings or writes."""

    raw = manifest_path.read_bytes()
    document = json.loads(raw)
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or not isinstance(document.get("models"), list)
    ):
        raise ValueError("The reviewed MiniMax H3 manifest must use schema_version 1")
    reviewed_at = document.get("reviewed_at")
    if not isinstance(reviewed_at, str) or date.fromisoformat(reviewed_at).isoformat() != reviewed_at:
        raise ValueError("The reviewed MiniMax H3 manifest needs an ISO review date")

    models: list[dict[str, Any]] = []
    seen_provider_ids: set[str] = set()
    seen_public_ids: set[str] = set()
    for entry in document["models"]:
        if not isinstance(entry, dict):
            raise ValueError("MiniMax H3 manifest model entries must be objects")
        if (
            entry.get("lifecycle") != "acceptance_candidate"
            or entry.get("evidence_status") != "route_acceptance_required"
            or entry.get("new_routes_allowed") is not True
        ):
            raise ValueError("MiniMax H3 route eligibility requires a reviewed candidate")
        provider_id, public_id = entry.get("provider_model_id"), entry.get("public_model_id")
        if not isinstance(provider_id, str) or not provider_id or not isinstance(public_id, str) or not public_id:
            raise ValueError("MiniMax H3 model identities are missing")
        if provider_id in seen_provider_ids or public_id in seen_public_ids:
            raise ValueError("MiniMax H3 manifest model identities must be unique")
        seen_provider_ids.add(provider_id)
        seen_public_ids.add(public_id)
        capability = RelayGenerationCapabilities.model_validate(entry.get("capability")).contract_dump()
        TaskCapabilityAdmission.validate_catalog({"generation": capability}, require_usable=True)
        if any(mode["limits"]["output_counts"] != [1] for mode in capability["modes"].values()):
            raise ValueError("MiniMax H3 per-second drafts require one output")
        request = AdminModelCreateRequest.model_validate({
            "slug": public_id,
            "display_name": entry.get("display_name"),
            "provider_key": "relay",
            "billing_mode": "per_second",
            "capabilities": [{"key": "generation", "config": capability}],
        })
        models.append({
            "provider_model_id": provider_id,
            "public_model_id": public_id,
            "adapter_profile_id": entry.get("adapter_profile_id"),
            "lifecycle": entry["lifecycle"],
            "evidence_status": entry["evidence_status"],
            "official_sources": entry.get("official_sources", []),
            "new_routes_allowed": True,
            "route_policy": "acceptance_required",
            "warning": "代码兼容不代表账号已开通、渠道已验收、已定价或已授权。",
            "admin_create_request": request.model_dump(mode="json"),
        })
    if not models:
        raise ValueError("The reviewed MiniMax H3 manifest has no draft candidates")
    return {
        "schema_version": 1,
        "preview_only": True,
        "source_manifest": (
            manifest_path.relative_to(REPOSITORY_ROOT).as_posix()
            if manifest_path.is_relative_to(REPOSITORY_ROOT) else manifest_path.name
        ),
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_at": reviewed_at,
        "provider_access_verified": False,
        "route_readiness_verified": False,
        "automatic_approval": False,
        "automatic_publish": False,
        "automatic_pricing": False,
        "automatic_distribution": False,
        "notice": (
            "只读离线声明预览，不是实时模型目录，不创建或发布模型。MiniMax API 开通、"
            "真实付费验收、经营价格及个人/企业授权均未由此验证；这些步骤需要分别审批。"
        ),
        "models": sorted(models, key=lambda model: model["public_model_id"]),
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        preview = prepare_draft_preview()
    except (OSError, ValueError, ConflictError) as exc:
        print(f"MiniMax H3 draft preview failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(preview, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
