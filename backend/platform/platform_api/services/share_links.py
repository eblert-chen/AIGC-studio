"""Anonymous read-only share links for finished generation results.

A share link is a bearer credential handed to somebody who has no account: a
client reviewing a cut, a producer outside the workspace. That asymmetry
drives every decision here.

* The raw token is generated, returned once, and never stored. Only a peppered
  digest is persisted, so a database leak cannot be replayed.
* Resolution is fail-closed. An unknown, expired, revoked, or re-pointed link
  answers 404 rather than explaining which of those it was, because telling an
  anonymous caller "expired" versus "wrong" is itself a disclosure.
* The public payload is redacted by construction: no task id, no prompt, no
  pricing, no cost, no storage key, no author. What leaves the building is
  what a reviewer needs to judge the work and nothing else.
* Every anonymous probe is metered and recorded. Guessing tokens is cheap for
  an attacker and expensive for us, so failures are counted per IP and a
  saturated source is refused before it reaches the database lookup.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AccountSecurityEvent,
    AuditOutcome,
    GenerationTask,
    ModelDefinition,
    ShareLink,
    SharePermission,
    ShareResourceType,
    TaskArtifact,
    TaskStatus,
    utcnow,
)
from .audit import AuditService
from .authentication import append_security_event
from .errors import ConflictError, NotFoundError

MIN_EXPIRY_SECONDS = 60
MAX_EXPIRY_SECONDS = 365 * 24 * 3600
DEFAULT_EXPIRY_SECONDS = 7 * 24 * 3600

# Anonymous token guessing is metered on a short window: a legitimate reviewer
# mistypes a URL a couple of times, a scanner tries thousands.
ANON_FAILURE_WINDOW_SECONDS = 300
ANON_FAILURE_LIMIT = 20

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$"


def _digest(value: str, *, pepper: str | None) -> str:
    """Mirror the invitation token digest so one pepper protects both."""

    raw = value.encode("utf-8")
    if pepper:
        return hmac.new(pepper.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


class CreateShareLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: Literal["task"] = "task"
    resource_id: str = Field(min_length=1, max_length=36)
    expires_in_seconds: int | None = Field(
        default=DEFAULT_EXPIRY_SECONDS,
        ge=MIN_EXPIRY_SECONDS,
        le=MAX_EXPIRY_SECONDS,
    )
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=IDEMPOTENCY_KEY_PATTERN)


class ShareLinkService:
    @staticmethod
    def _require_scope(company_id: str | None, personal_workspace_id: str | None) -> None:
        if (company_id is None) == (personal_workspace_id is None):
            raise ConflictError("分享链接必须且只能属于企业或个人空间之一")

    @classmethod
    def _require_shareable_task(
        cls,
        session: Session,
        *,
        resource_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
    ) -> GenerationTask:
        """Return the task only when it is finished and owned by this scope.

        A task belonging to another tenant is reported as missing rather than
        forbidden, so an identifier probe cannot learn that it exists.
        """

        task = session.get(GenerationTask, resource_id)
        if task is None:
            raise NotFoundError("分享的资源不存在")
        if company_id is not None:
            if task.company_id != company_id:
                raise NotFoundError("分享的资源不存在")
        elif task.personal_workspace_id != personal_workspace_id:
            raise NotFoundError("分享的资源不存在")
        if task.status != TaskStatus.SUCCEEDED:
            raise ConflictError("只有已成功完成的生成结果可以分享")
        return task

    @classmethod
    def _resource_still_shareable(cls, session: Session, *, link: ShareLink) -> bool:
        """Re-check ownership on every anonymous read.

        A link is issued against a resource that may later be deleted or moved.
        Possession of a valid token must not outlive the owner's right to
        expose the resource.
        """

        try:
            cls._require_shareable_task(
                session,
                resource_id=link.resource_id,
                company_id=link.company_id,
                personal_workspace_id=link.personal_workspace_id,
            )
        except (NotFoundError, ConflictError):
            return False
        return True

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        user_id: str,
        body: CreateShareLinkRequest,
        pepper: str | None,
        request_id: str = "system",
    ) -> tuple[ShareLink, str | None, bool]:
        cls._require_scope(company_id, personal_workspace_id)
        resource_type = ShareResourceType(body.resource_type)

        existing = session.scalar(
            select(ShareLink)
            .where(
                (
                    ShareLink.company_id == company_id
                    if company_id is not None
                    else ShareLink.personal_workspace_id == personal_workspace_id
                ),
                ShareLink.idempotency_key == body.idempotency_key,
            )
            .limit(1)
        )
        if existing is not None:
            # The raw token was returned exactly once and is unrecoverable;
            # replaying creation cannot mint a second copy of the same secret.
            return existing, None, False

        cls._require_shareable_task(
            session,
            resource_id=body.resource_id,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )

        raw_token = secrets.token_urlsafe(32)
        now = utcnow()
        link = ShareLink(
            token_digest=_digest(raw_token, pepper=pepper),
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            resource_type=resource_type,
            resource_id=body.resource_id,
            permission=SharePermission.READ,
            expires_at=(
                now + timedelta(seconds=body.expires_in_seconds)
                if body.expires_in_seconds
                else None
            ),
            revoked_at=None,
            created_by_user_id=user_id,
            access_count=0,
            last_accessed_at=None,
            idempotency_key=body.idempotency_key,
        )
        session.add(link)
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="share_link.create",
            target_type="share_link",
            target_id=link.id,
            before_summary={},
            after_summary={
                "scope": "company" if company_id else "personal",
                "resource_type": resource_type.value,
                "resource_id": body.resource_id,
                "permission": SharePermission.READ.value,
                "expires_at": (
                    link.expires_at.isoformat() if link.expires_at else None
                ),
            },
            request_id=request_id,
        )
        return link, raw_token, True

    @classmethod
    def list(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        resource_id: str | None = None,
    ) -> list[ShareLink]:
        cls._require_scope(company_id, personal_workspace_id)
        statement = select(ShareLink).where(
            ShareLink.company_id == company_id
            if company_id is not None
            else ShareLink.personal_workspace_id == personal_workspace_id
        )
        if resource_id is not None:
            statement = statement.where(ShareLink.resource_id == resource_id)
        return list(
            session.scalars(
                statement.order_by(ShareLink.created_at.desc(), ShareLink.id.desc())
            )
        )

    @classmethod
    def revoke(
        cls,
        session: Session,
        *,
        share_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
        user_id: str,
        request_id: str = "system",
    ) -> ShareLink:
        cls._require_scope(company_id, personal_workspace_id)
        link = session.scalar(
            select(ShareLink)
            .where(
                ShareLink.id == share_id,
                ShareLink.company_id == company_id
                if company_id is not None
                else ShareLink.personal_workspace_id == personal_workspace_id,
            )
            .limit(1)
        )
        if link is None:
            raise NotFoundError("分享链接不存在")
        if link.revoked_at is not None:
            return link
        link.revoked_at = utcnow()
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="share_link.revoke",
            target_type="share_link",
            target_id=link.id,
            before_summary={"revoked_at": None},
            after_summary={"revoked_at": link.revoked_at.isoformat()},
            request_id=request_id,
        )
        return link

    @classmethod
    def anonymous_failures_exceeded(cls, session: Session, *, ip_hash: str) -> bool:
        cutoff = utcnow() - timedelta(seconds=ANON_FAILURE_WINDOW_SECONDS)
        count = int(
            session.scalar(
                select(func.count(AccountSecurityEvent.id)).where(
                    AccountSecurityEvent.event_type == "share.access.rejected",
                    AccountSecurityEvent.ip_hash == ip_hash,
                    AccountSecurityEvent.created_at >= cutoff,
                )
            )
            or 0
        )
        return count >= ANON_FAILURE_LIMIT

    @classmethod
    def register_anonymous_failure(
        cls,
        session: Session,
        *,
        ip_hash: str,
        request_id: str,
        user_agent: str = "",
    ) -> None:
        append_security_event(
            session,
            event_type="share.access.rejected",
            user_id=None,
            outcome=AuditOutcome.FAILED,
            request_id=request_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
        session.flush()

    @classmethod
    def resolve(
        cls,
        session: Session,
        *,
        raw_token: str,
        pepper: str | None,
    ) -> ShareLink:
        """Resolve a raw token, failing closed for any unusable link."""

        candidate = (raw_token or "").strip()
        if not candidate:
            raise NotFoundError("分享链接不存在")
        link = session.scalar(
            select(ShareLink)
            .where(ShareLink.token_digest == _digest(candidate, pepper=pepper))
            .limit(1)
        )
        if link is None or not link.is_active:
            raise NotFoundError("分享链接不存在")
        if not cls._resource_still_shareable(session, link=link):
            raise NotFoundError("分享链接不存在")
        return link

    @classmethod
    def touch(cls, session: Session, *, link: ShareLink) -> None:
        link.access_count = int(link.access_count or 0) + 1
        link.last_accessed_at = utcnow()
        session.flush()

    @classmethod
    def artifact(
        cls,
        session: Session,
        *,
        link: ShareLink,
        position: int,
    ) -> TaskArtifact:
        if link.resource_type != ShareResourceType.TASK:
            raise NotFoundError("分享的资源不存在")
        artifact = session.scalar(
            select(TaskArtifact)
            .where(
                TaskArtifact.task_id == link.resource_id,
                TaskArtifact.position == position,
            )
            .limit(1)
        )
        if artifact is None or artifact.task_id != link.resource_id:
            raise NotFoundError("分享的资源不存在")
        # The artifact rows carry their own scope; a moved or re-scoped result
        # must not remain reachable through an older link.
        if link.company_id is not None and artifact.company_id != link.company_id:
            raise NotFoundError("分享的资源不存在")
        if (
            link.personal_workspace_id is not None
            and artifact.personal_workspace_id != link.personal_workspace_id
        ):
            raise NotFoundError("分享的资源不存在")
        return artifact

    @classmethod
    def public_payload(cls, session: Session, *, link: ShareLink) -> dict[str, Any]:
        """Build the redacted view an anonymous holder is allowed to see."""

        if link.resource_type != ShareResourceType.TASK:
            raise NotFoundError("分享的资源不存在")
        task = session.get(GenerationTask, link.resource_id)
        if task is None:
            raise NotFoundError("分享的资源不存在")
        model_name = session.scalar(
            select(ModelDefinition.display_name).where(
                ModelDefinition.id == task.model_id
            )
        )
        mode = None
        if isinstance(task.request_payload, dict):
            raw_mode = task.request_payload.get("mode")
            if isinstance(raw_mode, str) and raw_mode:
                mode = raw_mode
        artifacts = list(
            session.scalars(
                select(TaskArtifact)
                .where(TaskArtifact.task_id == task.id)
                .order_by(TaskArtifact.position)
            )
        )
        return {
            "resource_type": link.resource_type.value,
            "permission": link.permission.value,
            "status": task.status.value,
            # Deliberately absent: task id, prompt, capability/pricing
            # snapshots, every cost field, storage keys, and the author.
            "model": model_name,
            "mode": mode,
            "completed_at": task.updated_at.isoformat(),
            "media": [
                {
                    "position": item.position,
                    "media_type": item.media_type,
                    "content_type": item.content_type,
                    "size_bytes": int(item.size_bytes),
                }
                for item in artifacts
            ],
            "expires_at": (
                link.expires_at.isoformat() if link.expires_at is not None else None
            ),
            "access_count": int(link.access_count or 0),
        }

    @staticmethod
    def management_payload(link: ShareLink) -> dict[str, Any]:
        """Owner-facing view: enough to manage a link, never its token."""

        return {
            "id": link.id,
            "resource_type": link.resource_type.value,
            "resource_id": link.resource_id,
            "permission": link.permission.value,
            "expires_at": (
                link.expires_at.isoformat() if link.expires_at is not None else None
            ),
            "revoked_at": (
                link.revoked_at.isoformat() if link.revoked_at is not None else None
            ),
            "created_by_user_id": link.created_by_user_id,
            "access_count": int(link.access_count or 0),
            "last_accessed_at": (
                link.last_accessed_at.isoformat()
                if link.last_accessed_at is not None
                else None
            ),
            "created_at": link.created_at.isoformat(),
            "is_active": link.is_active,
        }
