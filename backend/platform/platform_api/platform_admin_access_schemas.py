from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


StrictModelConfig = ConfigDict(extra="forbid", str_strip_whitespace=True)
PermissionEffectValue = Literal["allow", "deny"]


class PlatformAdminPermissionResponse(BaseModel):
    model_config = StrictModelConfig

    code: str
    domain: str
    action: str
    description: str


class PlatformAdminRoleCreateRequest(BaseModel):
    model_config = StrictModelConfig

    key: Annotated[str, Field(min_length=2, max_length=80)]
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=1000)]
    permission_codes: list[str]
    change_reason: Annotated[str, Field(min_length=3, max_length=500)]

    @field_validator("permission_codes")
    @classmethod
    def reject_duplicate_permissions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("permission_codes must not contain duplicates")
        return value


class PlatformAdminRoleReplaceRequest(BaseModel):
    model_config = StrictModelConfig

    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=1000)]
    active: bool
    permission_codes: list[str]
    expected_lock_version: Annotated[int, Field(ge=1)]
    change_reason: Annotated[str, Field(min_length=3, max_length=500)]

    @field_validator("permission_codes")
    @classmethod
    def reject_duplicate_permissions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("permission_codes must not contain duplicates")
        return value


class PlatformAdminRoleResponse(BaseModel):
    model_config = StrictModelConfig

    id: str
    key: str
    display_name: str
    description: str
    active: bool
    lock_version: int
    permission_codes: list[str]


class PlatformAdminAccessReplaceRequest(BaseModel):
    model_config = StrictModelConfig

    role_ids: list[str]
    permission_overrides: dict[str, PermissionEffectValue]
    expected_lock_version: Annotated[int, Field(ge=0)]
    change_reason: Annotated[str, Field(min_length=3, max_length=500)]

    @field_validator("role_ids")
    @classmethod
    def reject_duplicate_roles(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("role_ids must not contain duplicates")
        return value


class PlatformAdminStatusRequest(BaseModel):
    model_config = StrictModelConfig

    enabled: bool
    expected_is_platform_admin: bool
    change_reason: Annotated[str, Field(min_length=3, max_length=500)]


class PlatformAdminAccessResponse(BaseModel):
    model_config = StrictModelConfig

    user_id: str
    is_platform_owner: bool
    lock_version: int
    role_ids: list[str]
    inherited_permissions: list[str]
    permission_overrides: dict[str, PermissionEffectValue]
    effective_permissions: list[str]
    snapshot: str


class PlatformAdministratorResponse(BaseModel):
    model_config = StrictModelConfig

    user_id: str
    email: str
    display_name: str
    status: Literal["active"]
    last_active_at: datetime | None
    access: PlatformAdminAccessResponse


class PlatformAdminStatusResponse(BaseModel):
    model_config = StrictModelConfig

    user_id: str
    is_platform_admin: bool
