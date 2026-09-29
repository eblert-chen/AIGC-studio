"""Reusable subjects and the reference images that pin their identity.

A subject is the layer this platform was missing between "a folder of uploads"
and "the same character in every shot". Without it, character consistency is an
accident of whoever uploaded which image last.

The interesting part of this module is not the CRUD; it is
``select_references``. Model support for reference images is wildly uneven --
Seedance 2.x accepts nine, most others accept one or two. A subject may
legitimately own more views than a given model can consume, so selecting
references is a *degradation*, not a validation:

* It never raises because a subject is richer than the model.
* It truncates to a deterministic prefix, so the same views are always
  preferred and results stay reproducible.
* The cover always wins the first slot, because a subject without its cover is
  no longer recognisably that subject.
* It reports whether it truncated, so a caller can tell the operator the model
  cannot use every view instead of silently producing a weaker result.

That last point is the difference between degrading and lying: the generation
still runs, but the shortfall is visible.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyModelGrant,
    InputAsset,
    ModelCapability,
    PersonalRetailModelGrant,
    Subject,
    SubjectKind,
    SubjectReference,
    SubjectViewAngle,
    utcnow,
)
from .errors import ConflictError, NotFoundError
from .task_admission import TaskCapabilityAdmission

NAME_PATTERN = r"^\S(?:.*\S)?$"
REFERENCE_ROLE = "reference_image"


class CreateSubjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["character", "style", "location", "prop"] = "character"
    name: str = Field(min_length=1, max_length=120, pattern=NAME_PATTERN)
    description: str = Field(default="", max_length=1000)
    cover_asset_id: str | None = None

    @property
    def resolved_kind(self) -> SubjectKind:
        return SubjectKind(self.kind)


class UpdateSubjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1000)
    cover_asset_id: str | None = None


class AddSubjectReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=36)
    view_angle: Literal["front", "side", "back", "three_quarter", "custom"] = "custom"
    sort_order: int | None = Field(default=None, ge=0)

    @property
    def resolved_angle(self) -> SubjectViewAngle:
        return SubjectViewAngle(self.view_angle)


class SubjectService:
    @staticmethod
    def _require_scope(company_id: str | None, personal_workspace_id: str | None) -> None:
        if (company_id is None) == (personal_workspace_id is None):
            raise ConflictError("主体必须且只能属于企业或个人空间之一")

    @classmethod
    def _require_subject(
        cls,
        session: Session,
        *,
        subject_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
        include_archived: bool = False,
    ) -> Subject:
        cls._require_scope(company_id, personal_workspace_id)
        subject = session.get(Subject, subject_id)
        if subject is None:
            raise NotFoundError("主体不存在")
        # Another tenant's subject is reported missing, not forbidden: an
        # identifier probe must not be able to confirm that it exists.
        if company_id is not None and subject.company_id != company_id:
            raise NotFoundError("主体不存在")
        if (
            personal_workspace_id is not None
            and subject.personal_workspace_id != personal_workspace_id
        ):
            raise NotFoundError("主体不存在")
        if not include_archived and subject.archived_at is not None:
            raise NotFoundError("主体不存在")
        return subject

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        user_id: str,
        body: CreateSubjectRequest,
    ) -> Subject:
        cls._require_scope(company_id, personal_workspace_id)
        subject = Subject(
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            kind=body.resolved_kind,
            name=body.name,
            description=body.description,
            cover_asset_id=body.cover_asset_id,
            created_by_user_id=user_id,
        )
        session.add(subject)
        session.flush()
        return subject

    @staticmethod
    def list(
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        kind: SubjectKind | None = None,
        include_archived: bool = False,
    ) -> list[Subject]:
        statement = select(Subject).where(
            Subject.company_id == company_id
            if company_id is not None
            else Subject.personal_workspace_id == personal_workspace_id
        )
        if kind is not None:
            statement = statement.where(Subject.kind == kind)
        if not include_archived:
            statement = statement.where(Subject.archived_at.is_(None))
        return list(
            session.scalars(statement.order_by(Subject.created_at.desc(), Subject.id))
        )

    @classmethod
    def update(
        cls,
        session: Session,
        *,
        subject_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
        body: UpdateSubjectRequest,
    ) -> Subject:
        subject = cls._require_subject(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        if body.name is not None:
            subject.name = body.name
        if body.description is not None:
            subject.description = body.description
        if body.cover_asset_id is not None:
            subject.cover_asset_id = body.cover_asset_id
        session.flush()
        return subject

    @classmethod
    def archive(
        cls,
        session: Session,
        *,
        subject_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
    ) -> Subject:
        subject = cls._require_subject(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        if subject.archived_at is None:
            subject.archived_at = utcnow()
            session.flush()
        return subject

    @classmethod
    def add_reference(
        cls,
        session: Session,
        *,
        subject_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
        body: AddSubjectReferenceRequest,
    ) -> SubjectReference:
        subject = cls._require_subject(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        asset = session.get(InputAsset, body.asset_id)
        # A subject may only be pinned to images its own scope can already see;
        # otherwise it would launder another tenant's asset into a generation.
        if asset is None or asset.company_id != subject.company_id:
            raise NotFoundError("参考素材不存在")
        if asset.personal_workspace_id != subject.personal_workspace_id:
            raise NotFoundError("参考素材不存在")
        if asset.media_type != "image":
            raise ConflictError("主体参考图必须是图片")
        existing = session.scalar(
            select(SubjectReference).where(
                SubjectReference.subject_id == subject.id,
                SubjectReference.asset_id == asset.id,
            )
        )
        if existing is not None:
            return existing
        sort_order = body.sort_order
        if sort_order is None:
            highest = session.scalar(
                select(SubjectReference.sort_order)
                .where(SubjectReference.subject_id == subject.id)
                .order_by(SubjectReference.sort_order.desc())
                .limit(1)
            )
            sort_order = 0 if highest is None else int(highest) + 1
        reference = SubjectReference(
            subject_id=subject.id,
            asset_id=asset.id,
            view_angle=body.resolved_angle,
            sort_order=sort_order,
        )
        session.add(reference)
        session.flush()
        return reference

    @staticmethod
    def remove_reference(
        session: Session,
        *,
        subject_id: str,
        asset_id: str,
    ) -> None:
        reference = session.scalar(
            select(SubjectReference).where(
                SubjectReference.subject_id == subject_id,
                SubjectReference.asset_id == asset_id,
            )
        )
        if reference is None:
            raise NotFoundError("主体参考图不存在")
        session.delete(reference)
        session.flush()

    @staticmethod
    def references(session: Session, *, subject_id: str) -> list[SubjectReference]:
        return list(
            session.scalars(
                select(SubjectReference)
                .where(SubjectReference.subject_id == subject_id)
                .order_by(SubjectReference.sort_order, SubjectReference.id)
            )
        )

    @classmethod
    def select_references(
        cls,
        session: Session,
        *,
        subject: Subject,
        max_images: int | None,
    ) -> tuple[list[SubjectReference], bool]:
        """Pick the references a model can actually consume.

        Returns the chosen references and whether the subject had more views
        than the model could take. Truncation is reported rather than raised:
        a model accepting one image must still be able to use a nine-view
        subject, just not to its full extent.
        """

        ordered = cls.references(session, subject_id=subject.id)
        if max_images is not None and max_images <= 0:
            return [], bool(ordered)
        cover_id = subject.cover_asset_id
        if cover_id is not None:
            # The cover leads: it is the view that defines the subject's
            # identity, so dropping it would change who the output depicts.
            ordered = [item for item in ordered if item.asset_id == cover_id] + [
                item for item in ordered if item.asset_id != cover_id
            ]
        if max_images is None or len(ordered) <= max_images:
            return ordered, False
        return ordered[:max_images], True

    @classmethod
    def reference_payload(
        cls,
        session: Session,
        *,
        subject: Subject,
        max_images: int | None,
        role: str | None = None,
    ) -> tuple[list[dict[str, str]], bool]:
        """References shaped for a generation request, plus a truncation flag.

        ``role`` is only set when the model actually declares
        ``reference_image``. Declaring it unconditionally would be rejected by
        admission -- and worse, would fail closed for every current model,
        because no shipped capability declares that role yet.
        """

        chosen, truncated = cls.select_references(
            session, subject=subject, max_images=max_images
        )
        return (
            [
                {
                    "asset_id": item.asset_id,
                    "media_type": "image",
                    **({"role": role} if role is not None else {}),
                }
                for item in chosen
            ],
            truncated,
        )

    @staticmethod
    def resolve_mode_capability(
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        model_id: str,
        mode: str,
    ) -> dict[str, Any] | None:
        """Return the effective capability for one model and mode.

        Returns None when the capability cannot be established, which callers
        treat as "do not expand" rather than "expand without a limit".
        """

        if (company_id is None) == (personal_workspace_id is None):
            return None
        if company_id is not None:
            grant = session.scalar(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.company_id == company_id,
                    CompanyModelGrant.model_id == model_id,
                )
            )
        else:
            # Retail grants are catalog-wide: there is no per-workspace row to
            # match, so the grant applies to every personal workspace that can
            # see the model. Selecting on a workspace column here would be
            # fiction -- and would silently disable expansion for everyone.
            grant = session.scalar(
                select(PersonalRetailModelGrant).where(
                    PersonalRetailModelGrant.model_id == model_id,
                )
            )
        override = (grant.config_override if grant is not None else None) or {}
        capability_map = {
            capability.capability_key: capability.config
            for capability in session.scalars(
                select(ModelCapability).where(ModelCapability.model_id == model_id)
            )
        }
        if not capability_map:
            return None
        # `validate` also validates a request, and an image mode with no assets
        # yet is exactly what we are trying to build -- it would reject its own
        # input. `effective_capabilities` answers the capability question alone.
        try:
            document = TaskCapabilityAdmission.effective_capabilities(
                capability_map=capability_map,
                config_override=override,
                require_usable=True,
            )
        except ConflictError:
            return None
        return (document.get("modes") or {}).get(mode)

    @classmethod
    def expand_request_payload(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        request_payload: dict[str, Any],
        model_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Expand referenced subjects into concrete image inputs.

        This is the step that turns a subject from a library entry into
        something a generation actually consumes. It runs before asset
        normalisation, so everything downstream -- admission validation and the
        dispatcher's signed-URL resolution -- sees ordinary asset references and
        needs no change.

        It is deliberately conservative. If the model's capability cannot be
        read, or the model accepts no images, or mixing roles would be
        rejected, it expands nothing and says why. Injecting references that
        admission would then refuse turns a graceful degradation into a
        confusing failure.

        Callers must run this *before* the idempotency replay lookup. The
        expanded assets are what actually get stored, so the stored request
        fingerprint is computed over them; expanding afterwards would leave a
        fingerprint no retry could ever reproduce. The consequence worth
        knowing is that the result depends on mutable state -- a subject's
        views and a model's declared image limit -- so editing either between
        two submissions of the same idempotency key makes the second one a
        conflict rather than a replay. That is the honest answer: the task
        those two requests describe is no longer the same task.
        """

        report: dict[str, Any] = {
            "requested": [],
            "expanded": 0,
            "truncated": False,
            "skipped": None,
        }
        raw_subject_ids = request_payload.get("subject_ids")
        if not raw_subject_ids:
            return request_payload, report
        if not isinstance(raw_subject_ids, list):
            raise ConflictError("subject_ids must be a list")
        subject_ids = [str(value) for value in raw_subject_ids]
        report["requested"] = subject_ids

        # Every exit below records its reason. A subject the operator asked for
        # must never silently vanish -- when nothing is expanded the payload
        # still carries why, because "the reference never made it into the
        # render" is otherwise indistinguishable from "the reference is wrong".
        def finish(
            payload: dict[str, Any], *, skipped: str | None = None
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            if skipped is not None:
                report["skipped"] = skipped
            stamped = dict(payload)
            stamped["subject_expansion"] = report
            return stamped, report

        mode = request_payload.get("mode") or "text_to_video"
        capability = cls.resolve_mode_capability(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            model_id=model_id,
            mode=mode,
        )
        if capability is None:
            return finish(
                request_payload, skipped="model capability is unavailable"
            )
        limits = capability.get("limits") or {}
        max_images = limits.get("max_images")
        if not isinstance(max_images, int):
            return finish(
                request_payload, skipped="model does not declare an image limit"
            )

        # Only claim the reference role when the capability allows it; every
        # shipped capability currently declares no input roles at all.
        allowed_roles = set(capability.get("input_roles") or [])
        role = REFERENCE_ROLE if REFERENCE_ROLE in allowed_roles else None

        assets = [dict(item) for item in (request_payload.get("assets") or [])]
        present_ids = {item.get("asset_id") for item in assets}
        existing_images = sum(
            1 for item in assets if item.get("media_type") == "image"
        )
        if any(item.get("role") for item in assets) and role is None:
            # Admission rejects mixing role-aware and unscoped inputs, so
            # appending unscoped references would break the whole request.
            return finish(
                request_payload,
                skipped="model does not declare the reference_image role",
            )

        budget = max_images - existing_images
        if budget <= 0:
            return finish(
                request_payload,
                skipped="model image budget is already fully used",
            )

        truncated = False
        for subject_id in subject_ids:
            if budget <= 0:
                truncated = True
                break
            subject = cls._require_subject(
                session,
                subject_id=subject_id,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
            )
            chosen, subject_truncated = cls.select_references(
                session, subject=subject, max_images=budget
            )
            truncated = truncated or subject_truncated
            for reference in chosen:
                if reference.asset_id in present_ids:
                    # Duplicate asset references are rejected upstream, so a
                    # view already supplied explicitly is simply not repeated.
                    continue
                if budget <= 0:
                    truncated = True
                    break
                assets.append(
                    {
                        "asset_id": reference.asset_id,
                        "media_type": "image",
                        **({"role": role} if role is not None else {}),
                    }
                )
                present_ids.add(reference.asset_id)
                budget -= 1
                report["expanded"] += 1

        report["truncated"] = truncated
        expanded = dict(request_payload)
        expanded["assets"] = assets
        return finish(expanded)

    @staticmethod
    def payload(
        subject: Subject,
        *,
        reference_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "id": subject.id,
            "kind": subject.kind.value,
            "name": subject.name,
            "description": subject.description,
            "cover_asset_id": subject.cover_asset_id,
            "archived_at": (
                subject.archived_at.isoformat()
                if subject.archived_at is not None
                else None
            ),
            "reference_count": reference_count,
            "created_by_user_id": subject.created_by_user_id,
            "created_at": subject.created_at.isoformat(),
        }

    @staticmethod
    def reference_payload_row(reference: SubjectReference) -> dict[str, Any]:
        return {
            "asset_id": reference.asset_id,
            "view_angle": reference.view_angle.value,
            "sort_order": int(reference.sort_order),
        }
