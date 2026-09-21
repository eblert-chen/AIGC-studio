#!/usr/bin/env python3
"""Preview reviewed Google model identities; never import or release them.

The versioned Platform manifest owns only provider attribution, display names
and the intended fixed-point billing shape.  It intentionally carries no model
capability.  An exact model must first appear in Relay's live, revision-bound
``/v1/models`` catalog before the catalog-sync worker can create an unpublished
draft from that live capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLATFORM_ROOT.parent.parent
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from platform_api.services.google_video_catalog import (  # noqa: E402
    DEFAULT_GOOGLE_VIDEO_MANIFEST,
    load_google_video_draft_specs,
)


DEFAULT_MANIFEST_PATH = DEFAULT_GOOGLE_VIDEO_MANIFEST


def prepare_draft_preview(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Return a no-write preview of the reviewed identity allowlist."""

    raw = manifest_path.read_bytes()
    document = json.loads(raw)
    specs = load_google_video_draft_specs(manifest_path)
    by_public_id = {
        entry["public_model_id"]: entry for entry in document["models"]
    }
    models = []
    for spec in specs:
        entry = by_public_id[spec.public_model_id]
        models.append(
            {
                "public_model_id": spec.public_model_id,
                "display_name": spec.display_name,
                "provider_key": spec.provider_key,
                "provider_model_ids": list(spec.provider_model_ids),
                "billing_mode": spec.billing_mode,
                "customer_pricing_policy": "admin_approved_fixed_points",
                "capability_source": "relay_live_catalog",
                "lifecycle": entry["lifecycle"],
                "evidence_status": entry["evidence_status"],
                "official_sources": list(spec.official_sources),
                "draft_creation_condition": "exact_live_relay_model_match",
                "warning": (
                    "模型身份已审阅，但 Google 账号、真实路由、供应商成本、"
                    "用户积分价格和个人/企业授权均未验收或批准。"
                ),
            }
        )
    return {
        "schema_version": 1,
        "preview_only": True,
        "source_manifest": (
            manifest_path.relative_to(REPOSITORY_ROOT).as_posix()
            if manifest_path.is_relative_to(REPOSITORY_ROOT)
            else manifest_path.name
        ),
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_at": document["reviewed_at"],
        "live_relay_catalog_supplied": False,
        "provider_access_verified": False,
        "route_readiness_verified": False,
        "automatic_approval": False,
        "automatic_publish": False,
        "automatic_pricing": False,
        "automatic_distribution": False,
        "notice": (
            "此预览不携带能力也不创建数据库记录。只有 Relay live catalog 中"
            "同名、带确定 revision 的模型才能形成待审草稿；其能力原样来自 Relay。"
        ),
        "models": models,
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        preview = prepare_draft_preview()
    except (OSError, ValueError) as exc:
        print(f"Google video draft preview failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(preview, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
