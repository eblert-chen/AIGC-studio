from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from .models import CompanyInvitationStatus, UserStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthUserResponse(StrictModel):
    id: str
    email: EmailStr
    display_name: str
    status: UserStatus
    email_verified_at: datetime | None
    auth_version: int


class AuthSessionResponse(StrictModel):
    authenticated: bool
    csrf_token: str | None = None
    user: AuthUserResponse | None = None
    account_management_url: str | None = None
    session_expires_at: datetime | None = None
    personal: dict | None = None
    companies: list[dict] = Field(default_factory=list)
    platform_admin: bool = False


class LogoutRequest(StrictModel):
    preserve_invitation: bool = False


class AccountResponse(AuthUserResponse):
    last_login_at: datetime | None
    updated_at: datetime


class AccountUpdateRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=120)
    expected_auth_version: int = Field(ge=1)
    expected_updated_at: datetime


class AccountDeactivateRequest(StrictModel):
    expected_auth_version: int = Field(ge=1)
    confirmation: Literal["DEACTIVATE"]


class EmptyRequest(StrictModel):
    pass


class AccountSessionResponse(StrictModel):
    id: str
    current: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    user_agent: str
    amr: list[str]
    auth_time: datetime | None


class AccountSessionPageResponse(StrictModel):
    items: list[AccountSessionResponse]
    page: int
    page_size: int
    total: int


class CompanyInvitationCreateRequest(StrictModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    primary_role: Literal["operator", "team_lead"] = "operator"
    idempotency_key: str = Field(min_length=8, max_length=120)
    expires_in_hours: int | None = Field(default=None, ge=1, le=720)


class CompanyInvitationResponse(StrictModel):
    id: str
    company_id: str
    email: EmailStr
    display_name: str
    primary_role: Literal["owner", "operator", "team_lead"]
    status: CompanyInvitationStatus
    expires_at: datetime
    created_by_user_id: str
    accepted_by_user_id: str | None
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    invitation_url: str | None = None


class CompanyInvitationPageResponse(StrictModel):
    items: list[CompanyInvitationResponse]
    page: int
    page_size: int
    total: int


class InvitationPreviewResponse(StrictModel):
    company_name: str
    email: EmailStr
    display_name: str
    primary_role: Literal["owner", "operator", "team_lead"]
    status: CompanyInvitationStatus
    expires_at: datetime


class InvitationAcceptResponse(StrictModel):
    company_id: str
    membership_id: str
    user_id: str
    status: Literal["accepted"] = "accepted"


class InvitationTokenRequest(StrictModel):
    token: str | None = Field(default=None, min_length=1, max_length=512)


class InvitationAcceptRequest(StrictModel):
    pass


class CompanyOwnerTransferRequest(StrictModel):
    target_membership_id: str = Field(min_length=1, max_length=36)
    expected_current_owner_membership_id: str = Field(min_length=1, max_length=36)
    expected_current_owner_user_id: str = Field(min_length=1, max_length=36)
    former_owner_primary_role: Literal["operator", "team_lead"]


class CompanyOwnerTransferResponse(StrictModel):
    company_id: str
    owner_membership_id: str
    owner_user_id: str
    former_owner_membership_id: str
    former_owner_user_id: str
    former_owner_primary_role: Literal["operator", "team_lead"]


class PlatformUserStatusUpdateRequest(StrictModel):
    expected_status: Literal["active", "suspended"]
    expected_auth_version: int = Field(ge=1)
    target_status: Literal["active", "suspended"]


class PlatformUserStatusResponse(AuthUserResponse):
    last_login_at: datetime | None
    deactivated_at: datetime | None
    updated_at: datetime


class PlatformUserPageResponse(StrictModel):
    items: list[PlatformUserStatusResponse]
    page: int
    page_size: int
    total: int


class OwnerOnboardingReissueRequest(StrictModel):
    expected_owner_membership_id: str = Field(min_length=1, max_length=36)
    expected_owner_user_id: str = Field(min_length=1, max_length=36)
    replacement_email: EmailStr | None = None
    replacement_display_name: str | None = Field(
        default=None, min_length=1, max_length=120
    )

    @model_validator(mode="after")
    def validate_replacement(self):
        if self.replacement_display_name is not None and self.replacement_email is None:
            raise ValueError("replacement_email is required when changing the owner")
        return self


class OwnerOnboardingInvitationResponse(StrictModel):
    company_id: str
    owner_membership_id: str
    owner_user_id: str
    invitation_id: str
    invitation_url: str
    expires_at: datetime
