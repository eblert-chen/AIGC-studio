"""Secret-free, versioned Platform -> Relay execution constraint.

The full contract lives in the private outbox. Only its digest is included in
the immutable customer quote; supplier account inventory is not customer data.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Mode = Literal[
    "text_to_image",
    "image_to_image",
    "text_to_video",
    "image_to_video",
    "video_to_video",
]


class ExecutionRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    route_id: str = Field(min_length=1, max_length=128)
    channel_id: int = Field(gt=0)
    provider_account_id: str = Field(min_length=1, max_length=128)
    provider_credential_set_version: str = Field(min_length=1, max_length=128)
    route_binding_sha256: Digest
    mode: Mode
    resolution: str = Field(min_length=1, max_length=32)
    cost_kind: Literal["contract_rate", "rate_set"]
    cost_id: str
    cost_sha256: Digest

    @field_validator("route_id", "provider_account_id", "provider_credential_set_version", "resolution")
    @classmethod
    def canonical_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("execution route identity must not contain surrounding whitespace")
        return value

    @field_validator("cost_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("cost id must be a canonical UUID")
        return value


class ExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    routing_release_sha256: Digest
    provider_cost_readiness_sha256: Digest
    routes: list[ExecutionRoute] = Field(min_length=1, max_length=64)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError("execution schema version must be an integer")
        return value

    @model_validator(mode="after")
    def canonical_routes(self) -> "ExecutionContract":
        ids = [route.route_id for route in self.routes]
        if ids != sorted(set(ids)):
            raise ValueError("execution routes must be sorted and unique")
        if len({(route.mode, route.resolution) for route in self.routes}) != 1:
            raise ValueError("execution routes must describe one exact mode and resolution")
        return self

    def content_sha256(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
