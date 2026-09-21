from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import TaskStatus


WorkspaceType = Literal["company", "personal"]


class PlatformAdminTaskContentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    workspace_type: WorkspaceType
    company_id: str | None
    company_name: str | None
    personal_workspace_id: str | None
    user_id: str
    user_display_name: str
    model_id: str
    model_display_name: str
    status: TaskStatus
    prompt: str | None
    prompt_length: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class PlatformAdminTaskContentPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=25)
    total: int = Field(ge=0)
    items: list[PlatformAdminTaskContentItem]
