from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from decimal import Decimal
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..director_canonical_json import director_canonical_json
from ..models import (
    DirectorShotPackage,
    GenerationTask,
    InputAsset,
    TaskDirectorShotPackage,
    new_director_shot_package_id,
)
from .errors import ConflictError, NotFoundError
from .input_assets import InputAssetService


SHA256_REFERENCE_PATTERN = r"^sha256:[0-9a-f]{64}$"
PACKAGE_ID_PATTERN = re.compile(r"^dsp_[0-9a-f]{32}$")
STABLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
POSE_CONTROL_KEYS = frozenset(
    {
        "body.pitch",
        "body.yaw",
        "body.roll",
        "torso.pitch",
        "torso.yaw",
        "torso.roll",
        "head.pitch",
        "head.yaw",
        "head.roll",
        "leftShoulder.pitch",
        "leftShoulder.spread",
        "leftShoulder.twist",
        "rightShoulder.pitch",
        "rightShoulder.spread",
        "rightShoulder.twist",
        "leftElbow.bend",
        "rightElbow.bend",
        "leftHip.pitch",
        "leftHip.spread",
        "leftHip.twist",
        "rightHip.pitch",
        "rightHip.spread",
        "rightHip.twist",
        "leftKnee.bend",
        "rightKnee.bend",
    }
)
POSE_PRESET_IDS = frozenset(
    {
        "stand",
        "t-pose",
        "walk",
        "run",
        "sit",
        "crouch",
        "kneel-one",
        "kneel-two",
        "hands-on-hips",
        "lean",
        "bow",
        "think",
        "fight",
        "kick",
        "throw",
        "push",
        "wave",
        "reach",
        "cross-arms",
        "phone",
    }
)


def _canonical_json(value: Any) -> bytes:
    try:
        return director_canonical_json(value)
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ConflictError("镜头约束包包含无法规范化的数据") from exc


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strip_sha256_prefix(value: str) -> str:
    return value.removeprefix("sha256:")


def _scene_revision_value(manifest: dict[str, Any]) -> dict[str, Any]:
    camera = dict(manifest["camera"])
    # v2 physical optics are sealed evidence, but the scene revision deliberately
    # remains the v1-compatible camera/object core so an old scene can be
    # re-sealed without changing its editor revision identity.
    camera.pop("optics", None)
    return {
        "projectSchemaVersion": manifest["source"]["projectSchemaVersion"],
        "camera": camera,
        "objects": manifest["objects"],
    }


def _camera_matches_composition_ratio(
    camera_aspect_ratio: float,
    width_px: int,
    height_px: int,
) -> bool:
    """Allow only float-representation noise around the exact pixel ratio."""

    return math.isclose(
        camera_aspect_ratio * height_px,
        float(width_px),
        rel_tol=1e-12,
        abs_tol=1e-9,
    )


def _parse_task_aspect_ratio(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    if not all(
        part.isascii() and part.isdigit() and 1 <= len(part) <= 9
        for part in parts
    ):
        return None
    numerator, denominator = (int(part) for part in parts)
    if numerator <= 0 or denominator <= 0:
        return None
    return numerator, denominator


def _dimensions_match_task_ratio(
    width_px: int,
    height_px: int,
    ratio: tuple[int, int],
) -> bool:
    numerator, denominator = ratio
    return width_px * denominator == height_px * numerator


def _go_director_float(value: float) -> str:
    """Mirror Go ``strconv.FormatFloat(value, 'g', -1, 64)``.

    Python and Go both compute the shortest round-trippable decimal, but use
    different fixed/scientific display thresholds. Reformat Python's shortest
    digits with Go's ``g/-1`` threshold so prompt-budget admission cannot drift.
    """

    negative = math.copysign(1.0, value) < 0
    if value == 0:
        return "-0" if negative else "0"
    decimal = Decimal(repr(abs(float(value))))
    digits = list(decimal.as_tuple().digits)
    exponent = int(decimal.as_tuple().exponent)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    rendered_digits = "".join(str(digit) for digit in digits) or "0"
    decimal_point = len(rendered_digits) + exponent
    scientific_exponent = decimal_point - 1
    if scientific_exponent < -4 or scientific_exponent >= 6:
        mantissa = rendered_digits[0]
        if len(rendered_digits) > 1:
            mantissa += "." + rendered_digits[1:]
        exponent_sign = "+" if scientific_exponent >= 0 else "-"
        rendered = (
            f"{mantissa}e{exponent_sign}{abs(scientific_exponent):02d}"
        )
    elif decimal_point <= 0:
        rendered = "0." + ("0" * -decimal_point) + rendered_digits
    elif decimal_point >= len(rendered_digits):
        rendered = rendered_digits + ("0" * (decimal_point - len(rendered_digits)))
    else:
        rendered = (
            rendered_digits[:decimal_point]
            + "."
            + rendered_digits[decimal_point:]
        )
    return ("-" if negative else "") + rendered


def _go_quote(value: str) -> str:
    """Mirror ``strconv.Quote`` for valid Python/UTF-8 strings."""

    escapes = {
        "\a": "\\a",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\v": "\\v",
        '"': '\\"',
        "\\": "\\\\",
    }
    output = ['"']
    for character in value:
        escaped = escapes.get(character)
        if escaped is not None:
            output.append(escaped)
            continue
        codepoint = ord(character)
        category = unicodedata.category(character)
        if character == " " or category[0] in {"L", "M", "N", "P", "S"}:
            output.append(character)
        elif codepoint < 0x80:
            output.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            output.append(f"\\u{codepoint:04x}")
        else:
            output.append(f"\\U{codepoint:08x}")
    output.append('"')
    return "".join(output)


def _director_vector(value: tuple[float, ...]) -> str:
    return "[" + ",".join(_go_director_float(item) for item in value) + "]"


def seedance_director_shot_prompt_v1(
    prompt: str,
    *,
    manifest: "DirectorShotManifest",
    manifest_sha256: str,
    sealed_revision: str,
) -> str:
    """Compile the exact current Seedance structured-director prompt contract."""

    coordinates = manifest.source.coordinateSystem
    camera = manifest.camera
    chunks = [
        prompt,
        "\n\n[DIRECTOR_SHOT_V1 manifest_sha256=",
        manifest_sha256,
        " sealed_revision=",
        sealed_revision,
        "]\n",
        "coordinates=handedness:",
        coordinates.handedness,
        ",up:",
        coordinates.upAxis,
        ",camera_forward:",
        coordinates.cameraForwardAxis,
        ",distance:",
        coordinates.distanceUnit,
        ",rotation:",
        coordinates.rotationUnit,
        "\nscene_revision=",
        manifest.source.sceneRevision,
        "\ncamera=id:",
        _go_quote(camera.stableId),
        ",name:",
        _go_quote(camera.name),
        ",projection:",
        camera.projection,
        ",vertical_fov_degrees:",
        _go_director_float(camera.verticalFovDegrees),
        ",aspect_ratio:",
        _go_director_float(camera.aspectRatio),
        ",position_m:",
        _director_vector(camera.view.positionMeters),
        ",target_m:",
        _director_vector(camera.view.targetMeters),
        ",up:",
        _director_vector(camera.view.up),
        "\n",
    ]
    for item in manifest.objects:
        transform = item.worldTransform
        chunks.extend(
            [
                "object=id:",
                _go_quote(item.stableId),
                ",name:",
                _go_quote(item.name),
                ",kind:",
                item.kind,
                ",visible:",
                "true" if item.visible else "false",
                ",position_m:",
                _director_vector(transform.positionMeters),
                ",rotation_quaternion:",
                _director_vector(transform.rotationQuaternion),
                ",scale:",
                _director_vector(transform.scale),
            ]
        )
        if item.pose is not None:
            chunks.extend([",pose_rig:", item.pose.rigType])
            if item.pose.presetId is not None:
                chunks.extend([",pose_preset:", _go_quote(item.pose.presetId)])
            for key in sorted(item.pose.controls):
                chunks.extend(
                    [
                        ",pose.",
                        key,
                        ":",
                        _go_director_float(item.pose.controls[key]),
                    ]
                )
        chunks.append("\n")
    composition = manifest.composition
    chunks.extend(
        [
            "composition=sha256:",
            _strip_sha256_prefix(composition.sha256),
            ",size_px:",
            str(composition.widthPx),
            "x",
            str(composition.heightPx),
            ",file:",
            _go_quote(composition.fileName),
            "\n[/DIRECTOR_SHOT_V1]",
        ]
    )
    return "".join(chunks)


def seedance_director_shot_prompt_v2(
    prompt: str,
    *,
    manifest: "DirectorShotManifest",
    manifest_sha256: str,
    sealed_revision: str,
) -> str:
    """Compile v2 without changing the byte-for-byte v1 replay contract."""

    if manifest.schemaVersion != 2 or manifest.camera.optics is None or not isinstance(
        manifest.analysis, DirectorPackageAnalysisV2
    ):
        raise ValueError("director_shot_v2 evidence is incomplete")
    base = seedance_director_shot_prompt_v1(
        prompt,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        sealed_revision=sealed_revision,
    ).replace("DIRECTOR_SHOT_V1", "DIRECTOR_SHOT_V2")
    optics = manifest.camera.optics
    chunks = [
        "\noptics=sensor_mm:",
        _director_vector((optics.sensorWidthMm, optics.sensorHeightMm)),
        ",focal_mm:",
        _go_director_float(optics.focalLengthMm),
        ",focus_m:",
        _go_director_float(optics.focusDistanceMeters),
    ]
    for item in manifest.analysis.depth.order:
        chunks.extend(
            [
                "\ndepth=id:",
                _go_quote(item.stableId),
                ",near_m:",
                _go_director_float(item.nearMeters),
                ",center_m:",
                _go_director_float(item.centerMeters),
                ",far_m:",
                _go_director_float(item.farMeters),
            ]
        )
    for item in manifest.analysis.occlusion.relations:
        chunks.extend(
            [
                "\nocclusion=front:",
                _go_quote(item.occluderStableId),
                ",back:",
                _go_quote(item.occludedStableId),
                ",overlap:",
                _go_director_float(item.overlapRatio),
            ]
        )
    return base.replace("\n[/DIRECTOR_SHOT_V2]", "".join(chunks) + "\n[/DIRECTOR_SHOT_V2]")


def seedance_director_motion_prompt_v1(
    prompt: str,
    *,
    motion: "DirectorMotionDocument",
    motion_sha256: str,
) -> str:
    start, end = (keyframe.state for keyframe in motion.keyframes)
    chunks = [
        prompt,
        "\n\n[DIRECTOR_MOTION_V1 motion_sha256=",
        motion_sha256,
        "]\nduration_ms=",
        str(motion.durationMs),
        ",preset:",
        "none" if motion.presetId is None else motion.presetId,
        ",object_rotation:quaternion_slerp",
    ]
    for label, camera in (("start", start.camera), ("end", end.camera)):
        chunks.extend(
            [
                "\ncamera_",
                label,
                "=id:",
                _go_quote(camera.stableId),
                ",position_m:",
                _director_vector(camera.positionMeters),
                ",target_m:",
                _director_vector(camera.targetMeters),
                ",vertical_fov_degrees:",
                _go_director_float(camera.verticalFovDegrees),
                ",focal_mm:",
                _go_director_float(camera.optics.focalLengthMm),
                ",focus_m:",
                _go_director_float(camera.optics.focusDistanceMeters),
            ]
        )
    for start_object, end_object in zip(start.objects, end.objects):
        chunks.extend(
            [
                "\nobject_motion=id:",
                _go_quote(start_object.stableId),
                ",start_position_m:",
                _director_vector(start_object.worldTransform.positionMeters),
                ",end_position_m:",
                _director_vector(end_object.worldTransform.positionMeters),
                ",start_rotation_quaternion:",
                _director_vector(start_object.worldTransform.rotationQuaternion),
                ",end_rotation_quaternion:",
                _director_vector(end_object.worldTransform.rotationQuaternion),
                ",start_scale:",
                _director_vector(start_object.worldTransform.scale),
                ",end_scale:",
                _director_vector(end_object.worldTransform.scale),
            ]
        )
        pose_keys = sorted(
            set(start_object.pose.controls if start_object.pose else {})
            | set(end_object.pose.controls if end_object.pose else {})
        )
        for key in pose_keys:
            chunks.extend(
                [
                    ",pose.",
                    key,
                    ":",
                    _go_director_float(
                        start_object.pose.controls.get(key, 0)
                        if start_object.pose
                        else 0
                    ),
                    "->",
                    _go_director_float(
                        end_object.pose.controls.get(key, 0)
                        if end_object.pose
                        else 0
                    ),
                ]
            )
    chunks.append("\n[/DIRECTOR_MOTION_V1]")
    return "".join(chunks)


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, populate_by_name=True
    )


FiniteNumber = Annotated[float, Field(ge=-1_000_000, le=1_000_000)]
Vector3 = tuple[FiniteNumber, FiniteNumber, FiniteNumber]
Quaternion = tuple[FiniteNumber, FiniteNumber, FiniteNumber, FiniteNumber]


class DirectorCoordinateSystem(_StrictModel):
    handedness: Literal["right"]
    upAxis: Literal["Y"]
    cameraForwardAxis: Literal["-Z"]
    distanceUnit: Literal["meter"]
    rotationUnit: Literal["radian"]


class DirectorPackageSource(_StrictModel):
    editor: Literal["storyai-3d-director-desk"]
    projectSchemaVersion: Literal[1]
    sceneRevision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    coordinateSystem: DirectorCoordinateSystem


class DirectorCameraView(_StrictModel):
    positionMeters: Vector3
    targetMeters: Vector3
    up: Vector3

    @model_validator(mode="after")
    def validate_view(self):
        if self.up != (0.0, 1.0, 0.0):
            raise ValueError("camera up must be [0, 1, 0]")
        distance = math.sqrt(
            sum(
                (target - position) ** 2
                for position, target in zip(self.positionMeters, self.targetMeters)
            )
        )
        if distance < 0.0001:
            raise ValueError("camera position and target must differ")
        return self


class DirectorPackageCamera(_StrictModel):
    stableId: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    projection: Literal["perspective"]
    verticalFovDegrees: float = Field(ge=5, le=160)
    aspectRatio: float = Field(ge=0.1, le=10)
    view: DirectorCameraView
    optics: "DirectorCameraOptics | None" = None

    @field_validator("stableId")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("camera stableId is invalid")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("camera name must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_optics(self):
        if self.optics is None:
            return self
        if not math.isclose(
            self.optics.sensorWidthMm / self.optics.sensorHeightMm,
            self.aspectRatio,
            rel_tol=1e-7,
            abs_tol=1e-7,
        ):
            raise ValueError("camera sensor aspect must match aspectRatio")
        expected_focal = 0.5 * self.optics.sensorHeightMm / math.tan(
            math.radians(self.verticalFovDegrees) / 2
        )
        if not math.isclose(
            self.optics.focalLengthMm,
            expected_focal,
            rel_tol=1e-6,
            abs_tol=1e-4,
        ):
            raise ValueError("camera focal length must match vertical FOV")
        focus_distance = math.sqrt(
            sum(
                (position - target) ** 2
                for position, target in zip(
                    self.view.positionMeters, self.view.targetMeters
                )
            )
        )
        if not math.isclose(
            self.optics.focusDistanceMeters,
            focus_distance,
            rel_tol=1e-6,
            abs_tol=1e-4,
        ):
            raise ValueError("camera focus distance must match camera target")
        return self


class DirectorCameraOptics(_StrictModel):
    sensorWidthMm: float = Field(gt=0, le=1_000)
    sensorHeightMm: float = Field(gt=0, le=1_000)
    focalLengthMm: float = Field(gt=0, le=10_000)
    focusDistanceMeters: float = Field(gt=0, le=1_000_000)


class DirectorWorldTransform(_StrictModel):
    positionMeters: Vector3
    rotationQuaternion: Quaternion
    scale: Vector3

    @field_validator("rotationQuaternion")
    @classmethod
    def validate_quaternion(cls, value: Quaternion) -> Quaternion:
        length = math.sqrt(sum(component * component for component in value))
        if not 0.999 <= length <= 1.001:
            raise ValueError("rotationQuaternion must be normalized")
        return value

    @field_validator("scale")
    @classmethod
    def validate_scale(cls, value: Vector3) -> Vector3:
        if any(component <= 0 or component > 1_000 for component in value):
            raise ValueError("scale must contain positive bounded values")
        return value


class DirectorObjectPose(_StrictModel):
    rigType: Literal["mannequin", "mixamo", "vrm", "custom-humanoid"]
    presetId: str | None = Field(default=None, min_length=1, max_length=80)
    controls: dict[str, float] = Field(default_factory=dict)

    @field_validator("controls")
    @classmethod
    def validate_controls(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 64:
            raise ValueError("pose controls exceed the supported limit")
        for key, amount in value.items():
            if key not in POSE_CONTROL_KEYS:
                raise ValueError(f"unsupported pose control: {key}")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise ValueError("pose control values must be numeric")
            if not math.isfinite(float(amount)) or not -4 * math.pi <= float(
                amount
            ) <= 4 * math.pi:
                raise ValueError("pose control value is outside the supported range")
        return value

    @field_validator("presetId")
    @classmethod
    def validate_preset_id(cls, value: str | None) -> str | None:
        if value is not None and value not in POSE_PRESET_IDS:
            raise ValueError("unsupported pose preset")
        return value


class DirectorPackageObject(_StrictModel):
    stableId: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["character", "scene", "prop", "camera", "panorama"]
    visible: bool
    worldTransform: DirectorWorldTransform
    pose: DirectorObjectPose | None = None

    @field_validator("stableId")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("object stableId is invalid")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("object name must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_pose_scope(self):
        if self.pose is not None and self.kind != "character":
            raise ValueError("only character objects may declare pose")
        return self


class DirectorCompositionManifest(_StrictModel):
    role: Literal["composition"]
    mediaType: Literal["image/png"]
    fileName: str = Field(min_length=1, max_length=255)
    widthPx: int = Field(ge=64, le=8_192)
    heightPx: int = Field(ge=64, le=8_192)
    byteLength: int = Field(ge=1, le=50 * 1024 * 1024)
    sha256: str = Field(pattern=SHA256_REFERENCE_PATTERN)

    @field_validator("fileName")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value != value.strip() or "/" in value or "\\" in value:
            raise ValueError("composition fileName must be a plain trimmed filename")
        return value


class DirectorAnalysisStatus(_StrictModel):
    status: Literal["not_provided"]


class DirectorPackageAnalysis(_StrictModel):
    depth: DirectorAnalysisStatus
    occlusion: DirectorAnalysisStatus


class DirectorAnalysisCapture(_StrictModel):
    widthPx: int = Field(ge=64, le=8_192)
    heightPx: int = Field(ge=64, le=8_192)
    aspectRatio: float = Field(ge=0.1, le=10)


class DirectorDepthItem(_StrictModel):
    stableId: str = Field(min_length=1, max_length=128)
    nearMeters: FiniteNumber
    centerMeters: FiniteNumber
    farMeters: FiniteNumber

    @field_validator("stableId")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("depth stableId is invalid")
        return value

    @model_validator(mode="after")
    def validate_interval(self):
        if not self.nearMeters <= self.centerMeters <= self.farMeters:
            raise ValueError("depth interval must be ordered")
        return self


class DirectorDepthAnalysis(_StrictModel):
    status: Literal["provided"]
    basis: Literal["camera_space_bounds_v1"]
    order: list[DirectorDepthItem] = Field(max_length=256)


class DirectorOcclusionRelation(_StrictModel):
    occluderStableId: str = Field(min_length=1, max_length=128)
    occludedStableId: str = Field(min_length=1, max_length=128)
    overlapRatio: float = Field(gt=0, le=1)

    @field_validator("occluderStableId", "occludedStableId")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("occlusion stableId is invalid")
        return value


class DirectorOcclusionAnalysis(_StrictModel):
    status: Literal["provided"]
    basis: Literal["screen_space_bounds_v1"]
    relations: list[DirectorOcclusionRelation] = Field(max_length=65_536)


class DirectorPackageAnalysisV2(_StrictModel):
    capture: DirectorAnalysisCapture
    depth: DirectorDepthAnalysis
    occlusion: DirectorOcclusionAnalysis


class DirectorShotManifest(_StrictModel):
    package_schema: Literal["xutian.director-shot-package"] = Field(alias="schema")
    schemaVersion: Literal[1, 2]
    source: DirectorPackageSource
    camera: DirectorPackageCamera
    objects: list[DirectorPackageObject] = Field(max_length=256)
    composition: DirectorCompositionManifest
    analysis: DirectorPackageAnalysis | DirectorPackageAnalysisV2

    @model_validator(mode="after")
    def validate_stable_ids(self):
        stable_ids = [item.stableId for item in self.objects]
        if len(stable_ids) != len(set(stable_ids)):
            raise ValueError("object stableId values must be unique")
        if not _camera_matches_composition_ratio(
            self.camera.aspectRatio,
            self.composition.widthPx,
            self.composition.heightPx,
        ):
            raise ValueError(
                "camera aspectRatio must match composition dimensions"
            )
        if self.schemaVersion == 1:
            if self.camera.optics is not None or not isinstance(
                self.analysis, DirectorPackageAnalysis
            ):
                raise ValueError("director_shot_v1 cannot contain v2 evidence")
            return self
        if self.camera.optics is None or not isinstance(
            self.analysis, DirectorPackageAnalysisV2
        ):
            raise ValueError("director_shot_v2 requires optics and scene analysis")
        capture = self.analysis.capture
        if (
            capture.widthPx != self.composition.widthPx
            or capture.heightPx != self.composition.heightPx
            or not _camera_matches_composition_ratio(
                capture.aspectRatio, capture.widthPx, capture.heightPx
            )
            or not math.isclose(
                capture.aspectRatio,
                self.camera.aspectRatio,
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
        ):
            raise ValueError("analysis capture must match composition and camera")
        expected_depth_ids = sorted(
            item.stableId
            for item in self.objects
            if item.visible and item.kind not in {"camera", "panorama"}
        )
        depth_ids = [item.stableId for item in self.analysis.depth.order]
        if sorted(depth_ids) != expected_depth_ids or len(depth_ids) != len(
            set(depth_ids)
        ):
            raise ValueError("depth analysis must cover every renderable object")
        canonical_depth = sorted(
            self.analysis.depth.order,
            key=lambda item: (item.centerMeters, item.stableId),
        )
        if depth_ids != [item.stableId for item in canonical_depth]:
            raise ValueError("depth analysis order must be canonical")
        depth_index = {stable_id: index for index, stable_id in enumerate(depth_ids)}
        relation_keys: list[tuple[str, str]] = []
        for relation in self.analysis.occlusion.relations:
            key = (relation.occluderStableId, relation.occludedStableId)
            if (
                key[0] == key[1]
                or key[0] not in depth_index
                or key[1] not in depth_index
                or depth_index[key[0]] >= depth_index[key[1]]
            ):
                raise ValueError("occlusion relation conflicts with depth order")
            relation_keys.append(key)
        if relation_keys != sorted(relation_keys) or len(relation_keys) != len(
            set(relation_keys)
        ):
            raise ValueError("occlusion relations must be unique and canonical")
        return self


class DirectorShotPackageIntegrity(_StrictModel):
    canonicalization: Literal["xutian-json-sort-v1"]
    manifest_sha256: str = Field(pattern=SHA256_REFERENCE_PATTERN)


class CreateDirectorShotPackageRequest(_StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    composition_asset_id: str = Field(min_length=1, max_length=64)
    manifest: dict[str, Any]
    integrity: DirectorShotPackageIntegrity

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("idempotency_key must contain visible non-space characters")
        return value

    @field_validator("manifest")
    @classmethod
    def validate_manifest(cls, value: dict[str, Any]) -> dict[str, Any]:
        DirectorShotManifest.model_validate(value)
        return value


class DirectorShotPackageResponse(_StrictModel):
    package_id: str
    company_id: str | None
    personal_workspace_id: str | None
    composition_asset_id: str
    manifest: dict[str, Any]
    manifest_sha256: str
    scene_revision: str
    sealed_revision: str
    created_at: datetime


class DirectorShotPackageReference(_StrictModel):
    package_id: str = Field(pattern=r"^dsp_[0-9a-f]{32}$")
    manifest_sha256: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    sealed_revision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    schema_version: Literal[2] | None = None


class DirectorTemporalFrameBinding(_StrictModel):
    role: Literal["first_frame", "last_frame"]
    asset_id: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=SHA256_REFERENCE_PATTERN)


class DirectorMotionPackageBinding(_StrictModel):
    packageId: str = Field(pattern=r"^dsp_[0-9a-f]{32}$")
    manifestSha256: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    sealedRevision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    sceneRevision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    cameraStableId: str = Field(min_length=1, max_length=128)

    @field_validator("cameraStableId")
    @classmethod
    def validate_camera_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("motion cameraStableId is invalid")
        return value


class DirectorMotionCamera(_StrictModel):
    stableId: str = Field(min_length=1, max_length=128)
    projection: Literal["perspective"]
    verticalFovDegrees: float = Field(ge=5, le=160)
    aspectRatio: float = Field(ge=0.1, le=10)
    positionMeters: Vector3
    targetMeters: Vector3
    optics: DirectorCameraOptics

    @field_validator("stableId")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("motion camera stableId is invalid")
        return value

    @model_validator(mode="after")
    def validate_camera(self):
        view = DirectorCameraView(
            positionMeters=self.positionMeters,
            targetMeters=self.targetMeters,
            up=(0.0, 1.0, 0.0),
        )
        DirectorPackageCamera(
            stableId=self.stableId,
            name="motion-camera",
            projection=self.projection,
            verticalFovDegrees=self.verticalFovDegrees,
            aspectRatio=self.aspectRatio,
            view=view,
            optics=self.optics,
        )
        return self


class DirectorMotionObject(_StrictModel):
    stableId: str = Field(min_length=1, max_length=128)
    kind: Literal["character", "scene", "prop", "camera", "panorama"]
    visible: bool
    worldTransform: DirectorWorldTransform
    pose: DirectorObjectPose | None = None

    @field_validator("stableId")
    @classmethod
    def validate_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("motion object stableId is invalid")
        return value

    @model_validator(mode="after")
    def validate_pose_scope(self):
        if self.pose is not None and self.kind != "character":
            raise ValueError("only motion character objects may declare pose")
        return self


class DirectorMotionState(_StrictModel):
    camera: DirectorMotionCamera
    objects: list[DirectorMotionObject] = Field(max_length=256)

    @model_validator(mode="after")
    def validate_ids(self):
        stable_ids = [item.stableId for item in self.objects]
        if len(stable_ids) != len(set(stable_ids)):
            raise ValueError("motion object stableId values must be unique")
        return self


class DirectorMotionKeyframe(_StrictModel):
    offsetMs: int = Field(ge=0, le=12_000)
    state: DirectorMotionState


class DirectorMotionInterpolation(_StrictModel):
    cameraPosition: Literal["linear"]
    cameraTarget: Literal["linear"]
    verticalFov: Literal["linear"]
    objectPosition: Literal["linear"]
    objectRotation: Literal["quaternion_slerp"]
    objectScale: Literal["linear"]
    poseControls: Literal["linear"]


class DirectorMotionDocument(_StrictModel):
    motion_schema: Literal["xutian.director-motion-document"] = Field(
        alias="schema"
    )
    schemaVersion: Literal[1]
    packageBinding: DirectorMotionPackageBinding
    coordinateSystem: DirectorCoordinateSystem
    durationMs: int = Field(ge=2_000, le=12_000)
    presetId: Literal["push_in", "pull_out", "truck", "orbit", "follow"] | None
    interpolation: DirectorMotionInterpolation
    keyframes: list[DirectorMotionKeyframe] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_keyframes(self):
        if [item.offsetMs for item in self.keyframes] != [0, self.durationMs]:
            raise ValueError("motion keyframes must be start and duration endpoints")
        start, end = (item.state for item in self.keyframes)
        start_topology = [
            (item.stableId, item.kind, item.visible) for item in start.objects
        ]
        end_topology = [
            (item.stableId, item.kind, item.visible) for item in end.objects
        ]
        if (
            start.camera.stableId != end.camera.stableId
            or not math.isclose(
                start.camera.aspectRatio,
                end.camera.aspectRatio,
                rel_tol=0,
                abs_tol=1e-9,
            )
            or start_topology != end_topology
        ):
            raise ValueError("motion start/end topology must be identical")
        return self


class DirectorTemporalBinding(_StrictModel):
    binding_schema: Literal["xutian.director-temporal-binding"] = Field(
        alias="schema"
    )
    schema_version: Literal[1, 2]
    package_id: str = Field(pattern=r"^dsp_[0-9a-f]{32}$")
    manifest_sha256: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    sealed_revision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    scene_revision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    camera_stable_id: str = Field(min_length=1, max_length=128)
    temporal_revision: str = Field(pattern=SHA256_REFERENCE_PATTERN)
    frames: list[DirectorTemporalFrameBinding] = Field(min_length=1, max_length=2)
    motion_sha256: str | None = Field(
        default=None, pattern=SHA256_REFERENCE_PATTERN
    )
    motion_document: DirectorMotionDocument | None = None

    @field_validator("camera_stable_id")
    @classmethod
    def validate_camera_stable_id(cls, value: str) -> str:
        if STABLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("camera_stable_id is invalid")
        return value

    @model_validator(mode="after")
    def validate_frame_roles(self):
        roles = [frame.role for frame in self.frames]
        if len(roles) != len(set(roles)):
            raise ValueError("director temporal frame roles must be unique")
        if roles != [
            role for role in ("first_frame", "last_frame") if role in roles
        ]:
            raise ValueError("director temporal frames must use canonical role order")
        if self.schema_version == 1:
            if self.motion_sha256 is not None or self.motion_document is not None:
                raise ValueError("temporal binding v1 cannot contain motion document")
        elif (
            self.motion_sha256 is None
            or self.motion_document is None
            or roles != ["first_frame", "last_frame"]
        ):
            raise ValueError(
                "temporal binding v2 requires motion document and both endpoint frames"
            )
        return self


class DirectorShotPackageService:
    @staticmethod
    def _scope_filters(
        *, company_id: str | None, personal_workspace_id: str | None
    ) -> tuple[Any, Any]:
        if (company_id is None) == (personal_workspace_id is None):
            raise RuntimeError("A director shot package requires exactly one scope")
        if company_id is not None:
            return (
                DirectorShotPackage.company_id == company_id,
                DirectorShotPackage.personal_workspace_id.is_(None),
            )
        return (
            DirectorShotPackage.company_id.is_(None),
            DirectorShotPackage.personal_workspace_id == personal_workspace_id,
        )

    @staticmethod
    def _request_fingerprint(body: CreateDirectorShotPackageRequest) -> str:
        return _sha256_hex(
            {
                "composition_asset_id": body.composition_asset_id,
                "manifest": body.manifest,
                "manifest_sha256": body.integrity.manifest_sha256,
            }
        )

    @classmethod
    def _require_composition_asset(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        asset_id: str,
        manifest: DirectorShotManifest,
    ) -> InputAsset:
        """Resolve the sealed composition against current trusted media facts.

        Packages created before media metadata v1 deliberately remain readable,
        but cannot enter a new task until their original composition has trusted
        decoded metadata. This prevents an old signature-only PNG from bypassing
        the newer task admission boundary.
        """

        locked_assets = InputAssetService.lock_scope_assets(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            asset_ids=[asset_id],
            require_active=True,
        )
        if not locked_assets:
            raise NotFoundError("镜头构图素材不存在或不属于当前空间")
        asset = locked_assets[0]
        if asset.media_type != "image" or asset.content_type != "image/png":
            raise ConflictError("镜头约束包的构图素材必须是 PNG 图片")
        if asset.sha256 != _strip_sha256_prefix(manifest.composition.sha256):
            raise ConflictError("镜头构图素材哈希与清单不一致")
        if asset.size_bytes != manifest.composition.byteLength:
            raise ConflictError("镜头构图素材大小与清单不一致")
        if (
            asset.media_metadata_version != 1
            or asset.width_px is None
            or asset.height_px is None
        ):
            raise ConflictError("镜头构图素材缺少服务端可信解码尺寸")
        if (
            asset.width_px != manifest.composition.widthPx
            or asset.height_px != manifest.composition.heightPx
        ):
            raise ConflictError("镜头构图素材像素尺寸与清单不一致")
        return asset

    @staticmethod
    def _sealed_revision(
        *,
        package_id: str,
        composition_asset_id: str,
        manifest_sha256: str,
        scene_revision_sha256: str,
    ) -> str:
        return _sha256_hex(
            {
                "composition_asset_id": composition_asset_id,
                "manifest_sha256": f"sha256:{manifest_sha256}",
                "package_id": package_id,
                "scene_revision": f"sha256:{scene_revision_sha256}",
                "schema": "xutian.director-shot-package-seal",
                "schema_version": 1,
            }
        )

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        user_id: str,
        body: CreateDirectorShotPackageRequest,
    ) -> tuple[DirectorShotPackage, bool]:
        cls._scope_filters(
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        manifest_model = DirectorShotManifest.model_validate(body.manifest)
        computed_manifest_sha = _sha256_hex(body.manifest)
        declared_manifest_sha = _strip_sha256_prefix(
            body.integrity.manifest_sha256
        )
        if computed_manifest_sha != declared_manifest_sha:
            raise ConflictError("镜头约束包清单哈希不匹配")
        computed_scene_revision = _sha256_hex(_scene_revision_value(body.manifest))
        if (
            manifest_model.schemaVersion == 2
            and computed_scene_revision
            != _strip_sha256_prefix(manifest_model.source.sceneRevision)
        ):
            raise ConflictError("导演场景修订摘要不匹配")

        request_fingerprint = cls._request_fingerprint(body)
        existing = session.scalar(
            select(DirectorShotPackage).where(
                *cls._scope_filters(
                    company_id=company_id,
                    personal_workspace_id=personal_workspace_id,
                ),
                DirectorShotPackage.created_by_user_id == user_id,
                DirectorShotPackage.idempotency_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise ConflictError("幂等键已被另一份镜头约束包使用")
            return existing, False

        asset = cls._require_composition_asset(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            asset_id=body.composition_asset_id,
            manifest=manifest_model,
        )

        package_id = new_director_shot_package_id()
        scene_revision_sha = (
            computed_scene_revision
            if manifest_model.schemaVersion == 2
            else _strip_sha256_prefix(manifest_model.source.sceneRevision)
        )
        package = DirectorShotPackage(
            id=package_id,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            created_by_user_id=user_id,
            composition_asset_id=asset.id,
            schema_version=manifest_model.schemaVersion,
            manifest=body.manifest,
            manifest_sha256=computed_manifest_sha,
            scene_revision_sha256=scene_revision_sha,
            sealed_revision_sha256=cls._sealed_revision(
                package_id=package_id,
                composition_asset_id=asset.id,
                manifest_sha256=computed_manifest_sha,
                scene_revision_sha256=scene_revision_sha,
            ),
            idempotency_key=body.idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        try:
            with session.begin_nested():
                session.add(package)
                session.flush()
        except IntegrityError:
            replay = session.scalar(
                select(DirectorShotPackage).where(
                    *cls._scope_filters(
                        company_id=company_id,
                        personal_workspace_id=personal_workspace_id,
                    ),
                    DirectorShotPackage.created_by_user_id == user_id,
                    DirectorShotPackage.idempotency_key == body.idempotency_key,
                )
            )
            if replay is None:
                raise
            if replay.request_fingerprint != request_fingerprint:
                raise ConflictError("幂等键已被另一份镜头约束包使用")
            return replay, False
        return package, True

    @classmethod
    def get(
        cls,
        session: Session,
        *,
        package_id: str,
        company_id: str | None,
        personal_workspace_id: str | None,
    ) -> DirectorShotPackage:
        if PACKAGE_ID_PATTERN.fullmatch(package_id) is None:
            raise NotFoundError("镜头约束包不存在")
        package = session.scalar(
            select(DirectorShotPackage).where(
                *cls._scope_filters(
                    company_id=company_id,
                    personal_workspace_id=personal_workspace_id,
                ),
                DirectorShotPackage.id == package_id,
            )
        )
        if package is None:
            raise NotFoundError("镜头约束包不存在")
        return package

    @staticmethod
    def response(package: DirectorShotPackage) -> dict[str, Any]:
        return {
            "package_id": package.id,
            "company_id": package.company_id,
            "personal_workspace_id": package.personal_workspace_id,
            "composition_asset_id": package.composition_asset_id,
            "manifest": package.manifest,
            "manifest_sha256": f"sha256:{package.manifest_sha256}",
            "scene_revision": f"sha256:{package.scene_revision_sha256}",
            "sealed_revision": f"sha256:{package.sealed_revision_sha256}",
            "created_at": package.created_at,
        }

    @staticmethod
    def relay_input(package: DirectorShotPackage | None) -> dict[str, Any] | None:
        """Return the sealed, URL-free constraint payload for Relay.

        The composition image itself continues through the private InputAsset
        resolver.  Keeping it separate prevents signed URLs or local model
        bytes from entering the immutable 3D constraint document.
        """

        if package is None:
            return None
        return {
            "manifest": package.manifest,
            "manifest_sha256": f"sha256:{package.manifest_sha256}",
            "sealed_revision": f"sha256:{package.sealed_revision_sha256}",
        }

    @staticmethod
    def motion_relay_input(request_payload: dict[str, Any]) -> dict[str, Any] | None:
        raw_binding = request_payload.get("director_temporal_binding")
        if raw_binding is None:
            return None
        try:
            binding = DirectorTemporalBinding.model_validate(raw_binding)
        except (TypeError, ValueError) as exc:
            raise ConflictError("director_temporal_binding is invalid") from exc
        if binding.schema_version != 2:
            return None
        assert binding.motion_document is not None and binding.motion_sha256 is not None
        return {
            "document": binding.motion_document.model_dump(mode="json", by_alias=True),
            "motion_sha256": binding.motion_sha256,
            "temporal_revision": binding.temporal_revision,
            "frames": [frame.model_dump(mode="json") for frame in binding.frames],
        }

    @staticmethod
    def canonicalize_task_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request_payload, dict):
            raise ConflictError("request_payload must be an object")
        normalized = dict(request_payload)
        raw_reference = request_payload.get("director_shot_package")
        if raw_reference is not None:
            try:
                reference = DirectorShotPackageReference.model_validate(raw_reference)
            except (TypeError, ValueError) as exc:
                raise ConflictError(
                    "director_shot_package reference is invalid"
                ) from exc
            normalized["director_shot_package"] = reference.model_dump(
                mode="json", exclude_none=True
            )
        raw_temporal_binding = request_payload.get("director_temporal_binding")
        if raw_temporal_binding is not None:
            try:
                temporal_binding = DirectorTemporalBinding.model_validate(
                    raw_temporal_binding
                )
            except (TypeError, ValueError) as exc:
                raise ConflictError(
                    "director_temporal_binding is invalid"
                ) from exc
            normalized["director_temporal_binding"] = temporal_binding.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        return normalized

    @classmethod
    def _validate_temporal_binding(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        package: DirectorShotPackage,
        manifest: DirectorShotManifest,
        request_payload: dict[str, Any],
    ) -> None:
        raw_binding = request_payload.get("director_temporal_binding")
        if raw_binding is None:
            if any(
                isinstance(item, dict)
                and item.get("role") in {"first_frame", "last_frame"}
                and item.get("asset_id") != package.composition_asset_id
                for item in request_payload.get("assets", [])
            ):
                raise ConflictError(
                    "导演起止画面必须提交服务端可验证的时间绑定"
                )
            return
        try:
            binding = DirectorTemporalBinding.model_validate(raw_binding)
        except (TypeError, ValueError) as exc:
            raise ConflictError("director_temporal_binding is invalid") from exc
        temporal_references = [
            item
            for item in request_payload.get("assets", [])
            if isinstance(item, dict)
            and item.get("role") in {"first_frame", "last_frame"}
            and (
                binding.schema_version == 2
                or item.get("asset_id") != package.composition_asset_id
            )
        ]
        if not temporal_references:
            raise ConflictError("导演时间绑定没有对应的起止画面素材")
        if (
            binding.package_id != package.id
            or binding.manifest_sha256 != f"sha256:{package.manifest_sha256}"
            or binding.sealed_revision
            != f"sha256:{package.sealed_revision_sha256}"
            or binding.scene_revision
            != f"sha256:{package.scene_revision_sha256}"
            or binding.camera_stable_id != manifest.camera.stableId
        ):
            raise ConflictError("导演时间绑定与封存镜头约束包不一致")

        if binding.schema_version == 2:
            motion = binding.motion_document
            assert motion is not None and binding.motion_sha256 is not None
            computed_motion_sha = f"sha256:{_sha256_hex(motion.model_dump(mode='json', by_alias=True))}"
            if computed_motion_sha != binding.motion_sha256:
                raise ConflictError("导演镜头运动文档摘要不匹配")
            motion_binding = motion.packageBinding
            if (
                motion_binding.packageId != package.id
                or motion_binding.manifestSha256
                != f"sha256:{package.manifest_sha256}"
                or motion_binding.sealedRevision
                != f"sha256:{package.sealed_revision_sha256}"
                or motion_binding.sceneRevision
                != f"sha256:{package.scene_revision_sha256}"
                or motion_binding.cameraStableId != manifest.camera.stableId
            ):
                raise ConflictError("导演镜头运动文档与封存镜头不一致")
            duration_seconds = request_payload.get("duration_seconds")
            if (
                isinstance(duration_seconds, bool)
                or not isinstance(duration_seconds, int)
                or motion.durationMs != duration_seconds * 1_000
            ):
                raise ConflictError("导演镜头运动时长与任务时长不一致")
            start = motion.keyframes[0].state
            camera = start.camera
            if (
                camera.stableId != manifest.camera.stableId
                or camera.projection != manifest.camera.projection
                or not math.isclose(
                    camera.verticalFovDegrees,
                    manifest.camera.verticalFovDegrees,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
                or not math.isclose(
                    camera.aspectRatio,
                    manifest.camera.aspectRatio,
                    rel_tol=0,
                    abs_tol=1e-9,
                )
                or camera.positionMeters != manifest.camera.view.positionMeters
                or camera.targetMeters != manifest.camera.view.targetMeters
                or (
                    manifest.schemaVersion == 2
                    and camera.optics != manifest.camera.optics
                )
            ):
                raise ConflictError("导演镜头运动起点相机不是封存相机")
            start_objects = [
                item.model_dump(mode="json", exclude_none=True)
                for item in start.objects
            ]
            sealed_objects = [
                {
                    "stableId": item.stableId,
                    "kind": item.kind,
                    "visible": item.visible,
                    "worldTransform": item.worldTransform.model_dump(mode="json"),
                    **(
                        {"pose": item.pose.model_dump(mode="json")}
                        if item.pose is not None
                        else {}
                    ),
                }
                for item in manifest.objects
            ]
            if start_objects != sealed_objects:
                raise ConflictError("导演镜头运动起点对象状态不是封存镜头")

        references_by_role = {
            item["role"]: item for item in temporal_references
        }
        frames_by_role = {frame.role: frame for frame in binding.frames}
        if set(references_by_role) != set(frames_by_role):
            raise ConflictError("导演时间绑定与任务起止画面角色不一致")
        for role, frame in frames_by_role.items():
            reference = references_by_role[role]
            if (
                reference.get("asset_id") != frame.asset_id
                or reference.get("media_type") != "image"
            ):
                raise ConflictError("导演时间绑定的素材身份与任务不一致")
        if binding.schema_version == 2 and (
            [frame.role for frame in binding.frames]
            != ["first_frame", "last_frame"]
            or binding.frames[0].asset_id != package.composition_asset_id
        ):
            raise ConflictError("导演镜头运动必须以封存构图为首帧并绑定结束画面")

        # Re-read every temporal asset under the same ascending InputAsset row
        # locks used by task admission/disable.  The endpoint transaction keeps
        # these rows locked through task links and wallet reservation.
        locked_assets = InputAssetService.lock_scope_assets(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            asset_ids=[frame.asset_id for frame in binding.frames],
            require_active=True,
        )
        assets_by_id = {asset.id: asset for asset in locked_assets}
        if len(assets_by_id) != len(binding.frames):
            raise NotFoundError("导演时间绑定的活动素材不存在于当前空间")
        for frame in binding.frames:
            asset = assets_by_id[frame.asset_id]
            if (
                asset.media_type != "image"
                or asset.sha256 != _strip_sha256_prefix(frame.sha256)
            ):
                raise ConflictError("导演时间绑定的素材摘要与服务端记录不一致")
            if (
                binding.schema_version == 2
                and (
                    asset.media_metadata_version != 1
                    or asset.width_px is None
                    or asset.height_px is None
                    or not _camera_matches_composition_ratio(
                        manifest.camera.aspectRatio,
                        asset.width_px,
                        asset.height_px,
                    )
                )
            ):
                raise ConflictError("导演镜头运动关键画面缺少可信且一致的画幅")

    @classmethod
    def require_for_task(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        request_payload: dict[str, Any],
    ) -> DirectorShotPackage | None:
        raw_reference = request_payload.get("director_shot_package")
        if raw_reference is None:
            if request_payload.get("director_temporal_binding") is not None:
                raise ConflictError(
                    "director_temporal_binding requires director_shot_package"
                )
            return None
        try:
            reference = DirectorShotPackageReference.model_validate(raw_reference)
        except (TypeError, ValueError) as exc:
            raise ConflictError("director_shot_package reference is invalid") from exc
        package = cls.get(
            session,
            package_id=reference.package_id,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        if reference.manifest_sha256 != f"sha256:{package.manifest_sha256}":
            raise ConflictError("镜头约束包清单版本不匹配")
        if reference.sealed_revision != f"sha256:{package.sealed_revision_sha256}":
            raise ConflictError("镜头约束包封存版本不匹配")
        if (
            (package.schema_version == 1 and reference.schema_version is not None)
            or (package.schema_version == 2 and reference.schema_version != 2)
            or package.schema_version not in {1, 2}
        ):
            raise ConflictError("镜头约束包结构版本不匹配")
        if _sha256_hex(package.manifest) != package.manifest_sha256:
            # Historical records remain readable and retain their original
            # seal. Never rewrite a signed identity or accept two alternative
            # hashes when admitting a new generation.
            raise ConflictError("历史镜头约束包规范化版本不兼容，请重新封存后生成")
        if (
            package.schema_version == 2
            and _sha256_hex(_scene_revision_value(package.manifest))
            != package.scene_revision_sha256
        ):
            raise ConflictError("镜头约束包场景修订无法重算，请重新封存后生成")
        try:
            manifest = DirectorShotManifest.model_validate(package.manifest)
        except (TypeError, ValueError) as exc:
            raise ConflictError("镜头约束包清单无法验证") from exc
        cls._require_composition_asset(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            asset_id=package.composition_asset_id,
            manifest=manifest,
        )
        task_ratio = _parse_task_aspect_ratio(request_payload.get("aspect_ratio"))
        if task_ratio is None:
            raise ConflictError("镜头约束包任务必须声明有效画面比例")
        if (
            not _dimensions_match_task_ratio(
                manifest.composition.widthPx,
                manifest.composition.heightPx,
                task_ratio,
            )
            or not _camera_matches_composition_ratio(
                manifest.camera.aspectRatio,
                manifest.composition.widthPx,
                manifest.composition.heightPx,
            )
        ):
            raise ConflictError("镜头约束包画面比例与任务画面比例不一致")
        prompt = request_payload.get("prompt")
        if not isinstance(prompt, str):
            raise ConflictError("镜头约束包任务必须包含文本提示词")
        shot_prompt_compiler = (
            seedance_director_shot_prompt_v2
            if manifest.schemaVersion == 2
            else seedance_director_shot_prompt_v1
        )
        provider_prompt = shot_prompt_compiler(
            prompt,
            manifest=manifest,
            manifest_sha256=f"sha256:{package.manifest_sha256}",
            sealed_revision=f"sha256:{package.sealed_revision_sha256}",
        )
        raw_temporal_binding = request_payload.get("director_temporal_binding")
        if isinstance(raw_temporal_binding, dict) and raw_temporal_binding.get(
            "schema_version"
        ) == 2:
            try:
                prompt_binding = DirectorTemporalBinding.model_validate(
                    raw_temporal_binding
                )
            except (TypeError, ValueError) as exc:
                raise ConflictError("director_temporal_binding is invalid") from exc
            assert (
                prompt_binding.motion_document is not None
                and prompt_binding.motion_sha256 is not None
            )
            provider_prompt = seedance_director_motion_prompt_v1(
                provider_prompt,
                motion=prompt_binding.motion_document,
                motion_sha256=prompt_binding.motion_sha256,
            )
        # Python ``len`` counts Unicode code points, matching Go's
        # utf8.RuneCountInString for this validated UTF-8 contract.
        if len(provider_prompt) > 2_000:
            raise ConflictError(
                "提示词与镜头约束包合计超过当前供应商 2000 字符限制"
            )
        matching_assets = [
            item
            for item in request_payload.get("assets", [])
            if isinstance(item, dict)
            and item.get("asset_id") == package.composition_asset_id
            and item.get("media_type") == "image"
            and item.get("role") in {"reference_image", "first_frame"}
        ]
        if len(matching_assets) != 1:
            raise ConflictError(
                "镜头约束包必须与同一构图素材及其生成角色一起提交"
            )
        cls._validate_temporal_binding(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            package=package,
            manifest=manifest,
            request_payload=request_payload,
        )
        return package

    @staticmethod
    def link_task(
        session: Session,
        *,
        task: GenerationTask,
        package: DirectorShotPackage | None,
    ) -> None:
        if package is None:
            return
        session.add(
            TaskDirectorShotPackage(
                task_id=task.id,
                package_id=package.id,
                manifest_sha256=package.manifest_sha256,
            )
        )
        session.flush()
