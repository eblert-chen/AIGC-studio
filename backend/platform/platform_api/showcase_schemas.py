from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ShowcaseSection = Literal["video", "template", "challenge"]
ShowcaseAspectRatio = Literal["auto", "1:1", "3:4", "4:3", "9:16", "16:9"]
ShowcaseCategory = Literal[
    "广告魔法",
    "电影叙事",
    "风格艺术",
    "动漫剧场",
    "数字人",
    "教育学习",
    "商品展示",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ShowcaseMediaResponse(_StrictModel):
    id: str
    source_task_artifact_id: str | None = None
    original_filename: str
    media_type: Literal["image", "video"]
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    content_url: str


class ShowcaseItemFields(_StrictModel):
    media_id: str = Field(min_length=36, max_length=36)
    title: str = Field(min_length=1, max_length=160)
    section: ShowcaseSection
    category: ShowcaseCategory
    alt_text: str = Field(min_length=1, max_length=300)
    public_prompt: str = Field(default="", max_length=2000)
    aspect_ratio: ShowcaseAspectRatio = "auto"
    is_hero: bool = False
    sort_order: int = Field(ge=0, le=2_147_483_647)

    @field_validator("title", "category", "alt_text")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("public_prompt")
    @classmethod
    def _trim_public_prompt(cls, value: str) -> str:
        return value.strip()


class ShowcaseItemCreateRequest(ShowcaseItemFields):
    expected_draft_version: int = Field(ge=0)


class ShowcaseItemUpdateRequest(ShowcaseItemFields):
    expected_draft_version: int = Field(ge=0)


class ShowcaseRetireRequest(_StrictModel):
    expected_draft_version: int = Field(ge=0)


class ShowcaseOrderRequest(_StrictModel):
    expected_draft_version: int = Field(ge=0)
    item_ids: list[str] = Field(min_length=1, max_length=500)

    @field_validator("item_ids")
    @classmethod
    def _unique_item_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(len(item_id) != 36 for item_id in value):
            raise ValueError("item_ids must contain unique canonical item identifiers")
        return value


class ShowcaseOrderResponse(_StrictModel):
    draft_version: int
    item_ids: list[str]


class ShowcasePublishRequest(_StrictModel):
    expected_draft_version: int = Field(ge=0)
    expected_publication_version: int = Field(ge=0)
    release_note: str = Field(min_length=1, max_length=500)

    @field_validator("release_note")
    @classmethod
    def _release_note_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("release_note must not be blank")
        return normalized


class ShowcaseDraftItemResponse(ShowcaseItemFields):
    id: str
    retired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    media: ShowcaseMediaResponse


class ShowcaseReleaseResponse(_StrictModel):
    id: str
    version: int
    draft_version: int
    publication_version: int
    published_by_user_id: str
    item_count: int
    source_release_id: str | None = None
    release_note: str
    manifest_sha256: str
    published_at: datetime


class ShowcaseUnpublishResponse(_StrictModel):
    id: str
    actor_user_id: str
    previous_release_id: str
    publication_version: int
    release_note: str
    unpublished_at: datetime


class ShowcaseAdminResponse(_StrictModel):
    draft_version: int
    publication_version: int
    has_unpublished_changes: bool
    current_release: ShowcaseReleaseResponse | None = None
    last_unpublished_event: ShowcaseUnpublishResponse | None = None
    publication_events: list[ShowcaseUnpublishResponse]
    items: list[ShowcaseDraftItemResponse]
    media: list[ShowcaseMediaResponse]
    releases: list[ShowcaseReleaseResponse]


class ShowcaseMutationResponse(_StrictModel):
    draft_version: int
    item: ShowcaseDraftItemResponse


class ShowcasePublicItem(_StrictModel):
    id: str
    title: str
    section: ShowcaseSection
    category: str
    alt_text: str
    public_prompt: str
    aspect_ratio: ShowcaseAspectRatio
    media_type: Literal["image", "video"]
    content_type: str
    media_url: str


class ShowcaseHomeResponse(_StrictModel):
    release_id: str | None = None
    version: int = 0
    published_at: datetime | None = None
    hero: ShowcasePublicItem | None = None
    items: list[ShowcasePublicItem] = Field(default_factory=list)
