from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ConflictError


SCHEMA_VERSION = 1
SUPPORTED_MODES = frozenset(
    {
        "text_to_image",
        "text_to_video",
        "image_to_video",
        "video_to_video",
    }
)
SUPPORTED_MEDIA_TYPES = frozenset({"image", "video", "audio"})
MAX_TOTAL_ASSETS = 15
MAX_DURATION_SECONDS = 3600
MAX_OUTPUT_COUNT = 16
MAX_PROMPT_LENGTH = 10_000
ASPECT_RATIO_PATTERN = re.compile(
    r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$"
)
RESOLUTION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
RESOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,118}[a-z0-9]$")


def _capability_error(message: str) -> ConflictError:
    return ConflictError(f"Model capability configuration is invalid: {message}")


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _mode_values(value: object, *, field_name: str) -> set[str]:
    raw_values = [value] if isinstance(value, str) else value
    if not isinstance(raw_values, list):
        raise _capability_error(f"{field_name} must be a string or list")
    modes: set[str] = set()
    for item in raw_values:
        if not isinstance(item, str):
            raise _capability_error(f"{field_name} contains a non-string value")
        mode = _normalized_key(item)
        if mode not in SUPPORTED_MODES:
            raise _capability_error(f"{field_name} contains an unsupported mode")
        modes.add(mode)
    if not modes:
        raise _capability_error(f"{field_name} must not be empty")
    return modes


def _string_values(
    value: object,
    *,
    field_name: str,
    allowed: frozenset[str] | None = None,
    pattern: re.Pattern[str] | None = None,
) -> set[str]:
    raw_values = [value] if isinstance(value, str) else value
    if not isinstance(raw_values, list):
        raise _capability_error(f"{field_name} must be a string or list")
    values: set[str] = set()
    for item in raw_values:
        if not isinstance(item, str):
            raise _capability_error(f"{field_name} contains a non-string value")
        if allowed is not None and item not in allowed:
            raise _capability_error(f"{field_name} contains an unsupported value")
        if pattern is not None and pattern.fullmatch(item) is None:
            raise _capability_error(f"{field_name} contains an unsafe value")
        values.add(item)
    return values


def _integer_values(
    value: object,
    *,
    field_name: str,
    maximum: int,
) -> set[int]:
    raw_values = (
        [value]
        if isinstance(value, int) and not isinstance(value, bool)
        else value
    )
    if not isinstance(raw_values, list):
        raise _capability_error(f"{field_name} must be an integer or list")
    values: set[int] = set()
    for item in raw_values:
        if (
            isinstance(item, bool)
            or not isinstance(item, int)
            or item <= 0
            or item > maximum
        ):
            raise _capability_error(
                f"{field_name} must contain integers between 1 and {maximum}"
            )
        values.add(item)
    return values


def _nonnegative_integer(
    value: object, *, field_name: str, maximum: int
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise _capability_error(
            f"{field_name} must be an integer between 0 and {maximum}"
        )
    return value


def _positive_integer(value: object, *, field_name: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > maximum
    ):
        raise _capability_error(
            f"{field_name} must be an integer between 1 and {maximum}"
        )
    return value


def _boolean(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise _capability_error(f"{field_name} must be a boolean")
    return value


def _resource_key_values(value: object, *, field_name: str) -> set[str]:
    if not isinstance(value, list):
        raise _capability_error(f"{field_name} must be a list")
    keys: set[str] = set()
    for item in value:
        if not isinstance(item, str) or RESOURCE_KEY_PATTERN.fullmatch(item) is None:
            raise _capability_error(f"{field_name} contains an invalid resource key")
        keys.add(item)
    return keys


@dataclass
class _Constraints:
    aspect_ratios: set[str] | None = None
    resolutions: set[str] | None = None
    durations: set[int] | None = None
    output_counts: set[int] | None = None
    input_media_types: set[str] | None = None
    max_duration_seconds: int | None = None
    min_duration_seconds: int | None = None
    max_prompt_length: int | None = None
    max_outputs: int | None = None
    max_images: int | None = None
    max_videos: int | None = None
    max_audio: int | None = None
    supports_face: bool | None = None
    required_resource_keys: set[str] = field(default_factory=set)

    def restrict_set(self, name: str, values: set[Any]) -> None:
        current = getattr(self, name)
        setattr(self, name, set(values) if current is None else current & values)

    def restrict_max(self, name: str, value: int) -> None:
        current = getattr(self, name)
        setattr(self, name, value if current is None else min(current, value))

    def restrict_min(self, name: str, value: int) -> None:
        current = getattr(self, name)
        setattr(self, name, value if current is None else max(current, value))

    def restrict_bool(self, name: str, value: bool) -> None:
        current = getattr(self, name)
        setattr(self, name, value if current is None else bool(current and value))

    def merge(self, other: "_Constraints") -> None:
        for name in (
            "aspect_ratios",
            "resolutions",
            "durations",
            "output_counts",
            "input_media_types",
        ):
            values = getattr(other, name)
            if values is not None:
                self.restrict_set(name, values)
        for name in (
            "max_duration_seconds",
            "max_prompt_length",
            "max_outputs",
            "max_images",
            "max_videos",
            "max_audio",
        ):
            value = getattr(other, name)
            if value is not None:
                self.restrict_max(name, value)
        if other.min_duration_seconds is not None:
            self.restrict_min(
                "min_duration_seconds", other.min_duration_seconds
            )
        if other.supports_face is not None:
            self.restrict_bool("supports_face", other.supports_face)
        self.required_resource_keys.update(other.required_resource_keys)


@dataclass
class _ModeCapability:
    input_media_types: set[str]
    supports_face: bool
    required_resource_keys: set[str]
    max_prompt_length: int
    max_images: int
    max_videos: int
    max_audio: int
    durations: set[int]
    aspect_ratios: set[str]
    resolutions: set[str]
    output_counts: set[int]

    def clone(self) -> "_ModeCapability":
        return _ModeCapability(
            input_media_types=set(self.input_media_types),
            supports_face=self.supports_face,
            required_resource_keys=set(self.required_resource_keys),
            max_prompt_length=self.max_prompt_length,
            max_images=self.max_images,
            max_videos=self.max_videos,
            max_audio=self.max_audio,
            durations=set(self.durations),
            aspect_ratios=set(self.aspect_ratios),
            resolutions=set(self.resolutions),
            output_counts=set(self.output_counts),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "input_media_types": sorted(self.input_media_types),
            "supports_face": self.supports_face,
            "required_resource_keys": sorted(self.required_resource_keys),
            "limits": {
                "max_prompt_length": self.max_prompt_length,
                "max_images": self.max_images,
                "max_videos": self.max_videos,
                "max_audio": self.max_audio,
                "duration_seconds": sorted(self.durations),
                "aspect_ratios": sorted(self.aspect_ratios),
                "resolutions": sorted(self.resolutions),
                "output_counts": sorted(self.output_counts),
            },
        }


@dataclass
class _ParsedLayer:
    constraints: _Constraints = field(default_factory=_Constraints)
    declared_modes: set[str] | None = None


class TaskCapabilityAdmission:
    _REQUEST_KEYS = frozenset(
        {
            "mode",
            "prompt",
            "assets",
            "duration_seconds",
            "aspect_ratio",
            "resolution",
            "output_count",
            "face_enabled",
            "metadata",
        }
    )
    _SET_ALIASES = {
        "aspect_ratios": ("aspect_ratios", "ratios", "aspect_ratio"),
        "resolutions": ("resolutions", "resolution"),
        "durations": ("duration_seconds", "durations", "duration"),
        "output_counts": ("output_counts", "output_count"),
        "input_media_types": ("input_media_types", "media_types"),
    }
    _MAX_ALIASES = {
        "max_duration_seconds": ("max_duration_seconds", "max_duration"),
        "max_prompt_length": ("max_prompt_length",),
        "max_outputs": ("max_outputs",),
        "max_images": ("max_images", "image"),
        "max_videos": ("max_videos", "video"),
        "max_audio": ("max_audio", "audio"),
    }
    _CANONICAL_MODE_KEYS = frozenset(
        {
            "input_media_types",
            "supports_face",
            "required_resource_keys",
            "limits",
        }
    )
    _CANONICAL_LIMIT_KEYS = frozenset(
        {
            "max_prompt_length",
            "max_images",
            "max_videos",
            "max_audio",
            "duration_seconds",
            "aspect_ratios",
            "resolutions",
            "output_counts",
        }
    )
    _LEGACY_CAPABILITY_KEYS = frozenset(
        {
            "generation",
            "duration",
            "durations",
            "duration_seconds",
            "aspect_ratio",
            "aspect_ratios",
            "ratios",
            "resolution",
            "resolutions",
            "output_count",
            "output_counts",
            "inputs",
            "input_media",
        }
    ) | SUPPORTED_MODES

    @classmethod
    def _document(cls, modes: dict[str, _ModeCapability]) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "modes": {
                mode: modes[mode].snapshot()
                for mode in sorted(modes)
            },
        }

    @staticmethod
    def _ensure_exact_keys(
        source: dict[str, Any], *, allowed: frozenset[str], field_name: str
    ) -> None:
        unknown = set(source) - set(allowed)
        if unknown:
            raise _capability_error(
                f"{field_name} contains unknown fields: {', '.join(sorted(unknown))}"
            )

    @classmethod
    def _validate_mode_capability(
        cls, mode: str, capability: _ModeCapability
    ) -> None:
        if not capability.durations:
            raise _capability_error(f"{mode} has no usable duration")
        if not capability.aspect_ratios:
            raise _capability_error(f"{mode} has no usable aspect ratio")
        if not capability.resolutions:
            raise _capability_error(f"{mode} has no usable resolution")
        if not capability.output_counts:
            raise _capability_error(f"{mode} has no usable output count")
        if (
            capability.max_images
            + capability.max_videos
            + capability.max_audio
            > MAX_TOTAL_ASSETS
        ):
            raise _capability_error(
                f"{mode} media limits exceed the total input limit"
            )
        limits = {
            "image": capability.max_images,
            "video": capability.max_videos,
            "audio": capability.max_audio,
        }
        for media_type, maximum in limits.items():
            declared = media_type in capability.input_media_types
            if declared != (maximum > 0):
                raise _capability_error(
                    f"{mode} {media_type} input declaration conflicts with its maximum"
                )
        if mode == "image_to_video" and capability.max_images == 0:
            raise _capability_error(
                "image_to_video must allow at least one image input"
            )
        if mode == "video_to_video" and capability.max_videos == 0:
            raise _capability_error(
                "video_to_video must allow at least one video input"
            )

    @classmethod
    def _canonical_mode(
        cls, mode: str, source: object
    ) -> _ModeCapability:
        if mode not in SUPPORTED_MODES:
            raise _capability_error(f"modes contains unsupported mode {mode}")
        if not isinstance(source, dict):
            raise _capability_error(f"modes.{mode} must contain an object")
        cls._ensure_exact_keys(
            source,
            allowed=cls._CANONICAL_MODE_KEYS,
            field_name=f"modes.{mode}",
        )
        missing = set(cls._CANONICAL_MODE_KEYS) - set(source)
        if missing:
            raise _capability_error(
                f"modes.{mode} is missing fields: {', '.join(sorted(missing))}"
            )
        limits = source["limits"]
        if not isinstance(limits, dict):
            raise _capability_error(f"modes.{mode}.limits must contain an object")
        cls._ensure_exact_keys(
            limits,
            allowed=cls._CANONICAL_LIMIT_KEYS,
            field_name=f"modes.{mode}.limits",
        )
        missing_limits = set(cls._CANONICAL_LIMIT_KEYS) - set(limits)
        if missing_limits:
            raise _capability_error(
                f"modes.{mode}.limits is missing fields: "
                f"{', '.join(sorted(missing_limits))}"
            )
        capability = _ModeCapability(
            input_media_types=_string_values(
                source["input_media_types"],
                field_name=f"modes.{mode}.input_media_types",
                allowed=SUPPORTED_MEDIA_TYPES,
            ),
            supports_face=_boolean(
                source["supports_face"],
                field_name=f"modes.{mode}.supports_face",
            ),
            required_resource_keys=_resource_key_values(
                source["required_resource_keys"],
                field_name=f"modes.{mode}.required_resource_keys",
            ),
            max_prompt_length=_positive_integer(
                limits["max_prompt_length"],
                field_name=f"modes.{mode}.limits.max_prompt_length",
                maximum=MAX_PROMPT_LENGTH,
            ),
            max_images=_nonnegative_integer(
                limits["max_images"],
                field_name=f"modes.{mode}.limits.max_images",
                maximum=MAX_TOTAL_ASSETS,
            ),
            max_videos=_nonnegative_integer(
                limits["max_videos"],
                field_name=f"modes.{mode}.limits.max_videos",
                maximum=MAX_TOTAL_ASSETS,
            ),
            max_audio=_nonnegative_integer(
                limits["max_audio"],
                field_name=f"modes.{mode}.limits.max_audio",
                maximum=MAX_TOTAL_ASSETS,
            ),
            durations=_integer_values(
                limits["duration_seconds"],
                field_name=f"modes.{mode}.limits.duration_seconds",
                maximum=MAX_DURATION_SECONDS,
            ),
            aspect_ratios=_string_values(
                limits["aspect_ratios"],
                field_name=f"modes.{mode}.limits.aspect_ratios",
                pattern=ASPECT_RATIO_PATTERN,
            ),
            resolutions=_string_values(
                limits["resolutions"],
                field_name=f"modes.{mode}.limits.resolutions",
                pattern=RESOLUTION_PATTERN,
            ),
            output_counts=_integer_values(
                limits["output_counts"],
                field_name=f"modes.{mode}.limits.output_counts",
                maximum=MAX_OUTPUT_COUNT,
            ),
        )
        cls._validate_mode_capability(mode, capability)
        return capability

    @classmethod
    def _canonical_catalog(
        cls, capability_map: dict[str, dict[str, Any]]
    ) -> dict[str, _ModeCapability] | None:
        generation = capability_map.get("generation")
        if not isinstance(generation, dict) or not isinstance(
            generation.get("modes"), dict
        ):
            return None
        if set(capability_map) != {"generation"}:
            raise _capability_error(
                "canonical generation capability cannot be mixed with legacy entries"
            )
        cls._ensure_exact_keys(
            generation,
            allowed=frozenset({"schema_version", "modes"}),
            field_name="generation",
        )
        if generation.get("schema_version") != SCHEMA_VERSION:
            raise _capability_error("generation.schema_version must be 1")
        raw_modes = generation["modes"]
        if not raw_modes:
            raise _capability_error("generation.modes must not be empty")
        return {
            mode: cls._canonical_mode(mode, source)
            for mode, source in raw_modes.items()
        }

    @classmethod
    def _consume_source(
        cls,
        constraints: _Constraints,
        source: dict[str, Any],
        *,
        field_name: str,
        strict: bool,
    ) -> None:
        known = {
            alias
            for aliases in cls._SET_ALIASES.values()
            for alias in aliases
        } | {
            alias
            for aliases in cls._MAX_ALIASES.values()
            for alias in aliases
        } | {"min_duration_seconds", "required_resource_keys", "supports_face"}
        if strict:
            unknown = set(source) - known
            if unknown:
                raise _capability_error(
                    f"{field_name} contains unknown fields: "
                    f"{', '.join(sorted(unknown))}"
                )
        for target, aliases in cls._SET_ALIASES.items():
            for alias in aliases:
                if alias not in source:
                    continue
                raw_value = source[alias]
                if target == "aspect_ratios":
                    values = _string_values(
                        raw_value,
                        field_name=f"{field_name}.{alias}",
                        pattern=ASPECT_RATIO_PATTERN,
                    )
                elif target == "resolutions":
                    values = _string_values(
                        raw_value,
                        field_name=f"{field_name}.{alias}",
                        pattern=RESOLUTION_PATTERN,
                    )
                elif target == "input_media_types":
                    values = _string_values(
                        raw_value,
                        field_name=f"{field_name}.{alias}",
                        allowed=SUPPORTED_MEDIA_TYPES,
                    )
                elif target == "durations":
                    values = _integer_values(
                        raw_value,
                        field_name=f"{field_name}.{alias}",
                        maximum=MAX_DURATION_SECONDS,
                    )
                else:
                    values = _integer_values(
                        raw_value,
                        field_name=f"{field_name}.{alias}",
                        maximum=MAX_OUTPUT_COUNT,
                    )
                constraints.restrict_set(target, values)

        for target, aliases in cls._MAX_ALIASES.items():
            for alias in aliases:
                if alias not in source:
                    continue
                if target == "max_prompt_length":
                    value = _positive_integer(
                        source[alias],
                        field_name=f"{field_name}.{alias}",
                        maximum=MAX_PROMPT_LENGTH,
                    )
                elif target == "max_duration_seconds":
                    value = _nonnegative_integer(
                        source[alias],
                        field_name=f"{field_name}.{alias}",
                        maximum=MAX_DURATION_SECONDS,
                    )
                elif target == "max_outputs":
                    value = _nonnegative_integer(
                        source[alias],
                        field_name=f"{field_name}.{alias}",
                        maximum=MAX_OUTPUT_COUNT,
                    )
                else:
                    value = _nonnegative_integer(
                        source[alias],
                        field_name=f"{field_name}.{alias}",
                        maximum=MAX_TOTAL_ASSETS,
                    )
                constraints.restrict_max(target, value)
        if "min_duration_seconds" in source:
            constraints.restrict_min(
                "min_duration_seconds",
                _positive_integer(
                    source["min_duration_seconds"],
                    field_name=f"{field_name}.min_duration_seconds",
                    maximum=MAX_DURATION_SECONDS,
                ),
            )
        if "supports_face" in source:
            constraints.restrict_bool(
                "supports_face",
                _boolean(
                    source["supports_face"],
                    field_name=f"{field_name}.supports_face",
                ),
            )
        if "required_resource_keys" in source:
            constraints.required_resource_keys.update(
                _resource_key_values(
                    source["required_resource_keys"],
                    field_name=f"{field_name}.required_resource_keys",
                )
            )

    @classmethod
    def _parse_layer(
        cls, key: str, config: object, *, strict: bool
    ) -> _ParsedLayer:
        if not isinstance(config, dict):
            raise _capability_error(f"{key} must contain an object")
        normalized_key = _normalized_key(key)
        if strict and normalized_key not in cls._LEGACY_CAPABILITY_KEYS:
            raise _capability_error(f"unknown capability key {key}")
        allowed_top = {
            alias
            for aliases in cls._SET_ALIASES.values()
            for alias in aliases
        } | {
            alias
            for aliases in cls._MAX_ALIASES.values()
            for alias in aliases
        } | {
            "min_duration_seconds",
            "required_resource_keys",
            "supports_face",
            "limits",
            "values",
            "modes",
            "mode",
        }
        if strict:
            unknown = set(config) - allowed_top
            if unknown:
                raise _capability_error(
                    f"{key} contains unknown fields: {', '.join(sorted(unknown))}"
                )
        parsed = _ParsedLayer()
        direct = {name: value for name, value in config.items() if name in allowed_top}
        for special in ("limits", "values", "modes", "mode"):
            direct.pop(special, None)
        cls._consume_source(
            parsed.constraints,
            direct,
            field_name=key,
            strict=strict,
        )
        limits = config.get("limits")
        if limits is not None:
            if not isinstance(limits, dict):
                raise _capability_error(f"{key}.limits must contain an object")
            cls._consume_source(
                parsed.constraints,
                limits,
                field_name=f"{key}.limits",
                strict=strict,
            )

        if "values" in config:
            if normalized_key in {"duration", "durations", "duration_seconds"}:
                parsed.constraints.restrict_set(
                    "durations",
                    _integer_values(
                        config["values"],
                        field_name=f"{key}.values",
                        maximum=MAX_DURATION_SECONDS,
                    ),
                )
            elif normalized_key in {"aspect_ratio", "aspect_ratios", "ratios"}:
                parsed.constraints.restrict_set(
                    "aspect_ratios",
                    _string_values(
                        config["values"],
                        field_name=f"{key}.values",
                        pattern=ASPECT_RATIO_PATTERN,
                    ),
                )
            elif normalized_key in {"resolution", "resolutions"}:
                parsed.constraints.restrict_set(
                    "resolutions",
                    _string_values(
                        config["values"],
                        field_name=f"{key}.values",
                        pattern=RESOLUTION_PATTERN,
                    ),
                )
            elif normalized_key in {"output_count", "output_counts"}:
                parsed.constraints.restrict_set(
                    "output_counts",
                    _integer_values(
                        config["values"],
                        field_name=f"{key}.values",
                        maximum=MAX_OUTPUT_COUNT,
                    ),
                )
            else:
                raise _capability_error(f"{key}.values is not supported")

        mode_value = config.get("modes", config.get("mode"))
        if mode_value is not None:
            parsed.declared_modes = _mode_values(
                mode_value, field_name=f"{key}.modes"
            )
        return parsed

    @classmethod
    def _legacy_model_modes(
        cls, capability_map: dict[str, dict[str, Any]], *, strict: bool
    ) -> set[str]:
        declared: set[str] = set()
        explicit = False
        for key, config in capability_map.items():
            normalized_key = _normalized_key(key)
            if normalized_key in SUPPORTED_MODES:
                declared.add(normalized_key)
                explicit = True
            parsed = cls._parse_layer(key, config, strict=strict)
            if parsed.declared_modes is not None:
                declared.update(parsed.declared_modes)
                explicit = True
        if explicit:
            return declared
        return {"text_to_video"} if capability_map else set()

    @classmethod
    def _legacy_constraints(
        cls,
        capability_map: dict[str, dict[str, Any]],
        *,
        mode: str,
        strict: bool,
    ) -> _Constraints:
        combined = _Constraints()
        for key, config in capability_map.items():
            normalized_key = _normalized_key(key)
            parsed = cls._parse_layer(key, config, strict=strict)
            if normalized_key in SUPPORTED_MODES and normalized_key != mode:
                continue
            if parsed.declared_modes is not None and mode not in parsed.declared_modes:
                continue
            combined.merge(parsed.constraints)
        return combined

    @classmethod
    def _legacy_mode(
        cls, mode: str, constraints: _Constraints
    ) -> _ModeCapability:
        minimum_duration = constraints.min_duration_seconds or 1
        maximum_duration = constraints.max_duration_seconds or MAX_DURATION_SECONDS
        durations = {5} if constraints.durations is None else set(constraints.durations)
        durations = {
            value
            for value in durations
            if minimum_duration <= value <= maximum_duration
        }

        maximum_outputs = constraints.max_outputs
        if maximum_outputs is None:
            maximum_outputs = (
                max(constraints.output_counts)
                if constraints.output_counts
                else 1
            )
        output_counts = (
            set(range(1, maximum_outputs + 1))
            if constraints.output_counts is None
            else set(constraints.output_counts)
        )
        output_counts = {
            value for value in output_counts if value <= maximum_outputs
        }

        required_media_type = {
            "image_to_video": "image",
            "video_to_video": "video",
        }.get(mode)
        explicit_media = constraints.input_media_types
        media_types = set(explicit_media or [])
        media_limits: dict[str, int] = {}
        for media_type, field_name in (
            ("image", "max_images"),
            ("video", "max_videos"),
            ("audio", "max_audio"),
        ):
            configured = getattr(constraints, field_name)
            if configured is None:
                configured = (
                    1
                    if media_type in media_types or media_type == required_media_type
                    else 0
                )
            media_limits[field_name] = configured
            if explicit_media is None and configured > 0:
                media_types.add(media_type)
            if configured == 0:
                media_types.discard(media_type)

        capability = _ModeCapability(
            input_media_types=media_types,
            supports_face=bool(constraints.supports_face),
            required_resource_keys=set(constraints.required_resource_keys),
            max_prompt_length=constraints.max_prompt_length or MAX_PROMPT_LENGTH,
            max_images=media_limits["max_images"],
            max_videos=media_limits["max_videos"],
            max_audio=media_limits["max_audio"],
            durations=durations,
            aspect_ratios=(
                {"16:9"}
                if constraints.aspect_ratios is None
                else set(constraints.aspect_ratios)
            ),
            resolutions=(
                {"720p"}
                if constraints.resolutions is None
                else set(constraints.resolutions)
            ),
            output_counts=output_counts,
        )
        cls._validate_mode_capability(mode, capability)
        return capability

    @classmethod
    def _catalog_modes(
        cls,
        capability_map: dict[str, dict[str, Any]],
        *,
        strict: bool,
    ) -> dict[str, _ModeCapability]:
        canonical = cls._canonical_catalog(capability_map)
        if canonical is not None:
            return canonical
        modes = cls._legacy_model_modes(capability_map, strict=strict)
        return {
            mode: cls._legacy_mode(
                mode,
                cls._legacy_constraints(
                    capability_map, mode=mode, strict=strict
                ),
            )
            for mode in sorted(modes)
        }

    @classmethod
    def _apply_canonical_override(
        cls,
        base_modes: dict[str, _ModeCapability],
        override: dict[str, Any],
    ) -> dict[str, _ModeCapability]:
        cls._ensure_exact_keys(
            override,
            allowed=frozenset({"schema_version", "modes"}),
            field_name="config_override",
        )
        if override.get("schema_version") != SCHEMA_VERSION:
            raise _capability_error("config_override.schema_version must be 1")
        raw_modes = override.get("modes")
        if not isinstance(raw_modes, dict) or not raw_modes:
            raise _capability_error("config_override.modes must be a non-empty object")
        unknown_modes = set(raw_modes) - set(base_modes)
        if unknown_modes:
            raise _capability_error(
                "config_override cannot add modes: "
                f"{', '.join(sorted(unknown_modes))}"
            )
        result: dict[str, _ModeCapability] = {}
        for mode, raw_spec in raw_modes.items():
            if not isinstance(raw_spec, dict):
                raise _capability_error(
                    f"config_override.modes.{mode} must contain an object"
                )
            cls._ensure_exact_keys(
                raw_spec,
                allowed=cls._CANONICAL_MODE_KEYS,
                field_name=f"config_override.modes.{mode}",
            )
            capability = base_modes[mode].clone()
            prefix = f"config_override.modes.{mode}"
            if "input_media_types" in raw_spec:
                requested = _string_values(
                    raw_spec["input_media_types"],
                    field_name=f"{prefix}.input_media_types",
                    allowed=SUPPORTED_MEDIA_TYPES,
                )
                if not requested <= capability.input_media_types:
                    raise _capability_error(
                        f"{prefix}.input_media_types cannot expand the model"
                    )
                capability.input_media_types &= requested
                for media_type, limit_name in (
                    ("image", "max_images"),
                    ("video", "max_videos"),
                    ("audio", "max_audio"),
                ):
                    if media_type not in capability.input_media_types:
                        setattr(capability, limit_name, 0)
                for media_type, attribute in (
                    ("image", "max_images"),
                    ("video", "max_videos"),
                    ("audio", "max_audio"),
                ):
                    if media_type not in requested:
                        setattr(capability, attribute, 0)
            if "supports_face" in raw_spec:
                requested_face = _boolean(
                    raw_spec["supports_face"],
                    field_name=f"{prefix}.supports_face",
                )
                if requested_face and not capability.supports_face:
                    raise _capability_error(
                        f"{prefix}.supports_face cannot expand the model"
                    )
                capability.supports_face = capability.supports_face and requested_face
            if "required_resource_keys" in raw_spec:
                capability.required_resource_keys.update(
                    _resource_key_values(
                        raw_spec["required_resource_keys"],
                        field_name=f"{prefix}.required_resource_keys",
                    )
                )
            if "limits" in raw_spec:
                raw_limits = raw_spec["limits"]
                if not isinstance(raw_limits, dict):
                    raise _capability_error(f"{prefix}.limits must contain an object")
                cls._ensure_exact_keys(
                    raw_limits,
                    allowed=cls._CANONICAL_LIMIT_KEYS,
                    field_name=f"{prefix}.limits",
                )
                for field_name, attribute in (
                    ("max_prompt_length", "max_prompt_length"),
                    ("max_images", "max_images"),
                    ("max_videos", "max_videos"),
                    ("max_audio", "max_audio"),
                ):
                    if field_name not in raw_limits:
                        continue
                    current = getattr(capability, attribute)
                    requested = (
                        _positive_integer(
                            raw_limits[field_name],
                            field_name=f"{prefix}.limits.{field_name}",
                            maximum=MAX_PROMPT_LENGTH,
                        )
                        if field_name == "max_prompt_length"
                        else _nonnegative_integer(
                            raw_limits[field_name],
                            field_name=f"{prefix}.limits.{field_name}",
                            maximum=MAX_TOTAL_ASSETS,
                        )
                    )
                    if requested > current:
                        raise _capability_error(
                            f"{prefix}.limits.{field_name} cannot expand the model"
                        )
                    setattr(capability, attribute, requested)
                set_fields = (
                    ("duration_seconds", "durations", MAX_DURATION_SECONDS, None),
                    ("aspect_ratios", "aspect_ratios", None, ASPECT_RATIO_PATTERN),
                    ("resolutions", "resolutions", None, RESOLUTION_PATTERN),
                    ("output_counts", "output_counts", MAX_OUTPUT_COUNT, None),
                )
                for field_name, attribute, maximum, pattern in set_fields:
                    if field_name not in raw_limits:
                        continue
                    requested = (
                        _integer_values(
                            raw_limits[field_name],
                            field_name=f"{prefix}.limits.{field_name}",
                            maximum=maximum,
                        )
                        if maximum is not None
                        else _string_values(
                            raw_limits[field_name],
                            field_name=f"{prefix}.limits.{field_name}",
                            pattern=pattern,
                        )
                    )
                    current = getattr(capability, attribute)
                    if not requested <= current:
                        raise _capability_error(
                            f"{prefix}.limits.{field_name} cannot expand the model"
                        )
                    setattr(capability, attribute, current & requested)
            if capability.max_images == 0:
                capability.input_media_types.discard("image")
            if capability.max_videos == 0:
                capability.input_media_types.discard("video")
            if capability.max_audio == 0:
                capability.input_media_types.discard("audio")
            cls._validate_mode_capability(mode, capability)
            result[mode] = capability
        return result

    @classmethod
    def _legacy_override_constraints(
        cls, override: dict[str, Any], *, mode: str
    ) -> tuple[set[str] | None, _Constraints]:
        combined = _Constraints()
        root = {
            key: value
            for key, value in override.items()
            if key not in {"capabilities", mode, mode.replace("_", "-")}
        }
        override_modes: set[str] | None = None
        if "modes" in root or "mode" in root:
            override_modes = _mode_values(
                root.get("modes", root.get("mode")),
                field_name="config_override.modes",
            )
        parsed_root = cls._parse_layer(
            "generation", root, strict=False
        )
        combined.merge(parsed_root.constraints)
        nested = override.get("capabilities")
        if isinstance(nested, dict):
            combined.merge(
                cls._legacy_constraints(nested, mode=mode, strict=False)
            )
        for key in (mode, mode.replace("_", "-")):
            mode_override = override.get(key)
            if mode_override is not None:
                combined.merge(
                    cls._parse_layer(key, mode_override, strict=False).constraints
                )
        return override_modes, combined

    @classmethod
    def _apply_legacy_constraints(
        cls, capability: _ModeCapability, constraints: _Constraints
    ) -> _ModeCapability:
        result = capability.clone()
        for name, attribute in (
            ("aspect_ratios", "aspect_ratios"),
            ("resolutions", "resolutions"),
            ("durations", "durations"),
            ("output_counts", "output_counts"),
            ("input_media_types", "input_media_types"),
        ):
            values = getattr(constraints, name)
            if values is not None:
                setattr(result, attribute, getattr(result, attribute) & values)
        if constraints.min_duration_seconds is not None:
            result.durations = {
                value
                for value in result.durations
                if value >= constraints.min_duration_seconds
            }
        if constraints.max_duration_seconds is not None:
            result.durations = {
                value
                for value in result.durations
                if value <= constraints.max_duration_seconds
            }
        for name in (
            "max_prompt_length",
            "max_images",
            "max_videos",
            "max_audio",
        ):
            value = getattr(constraints, name)
            if value is not None:
                setattr(result, name, min(getattr(result, name), value))
        if constraints.max_outputs is not None:
            result.output_counts = {
                value
                for value in result.output_counts
                if value <= constraints.max_outputs
            }
        if constraints.supports_face is not None:
            result.supports_face = result.supports_face and constraints.supports_face
        result.required_resource_keys.update(constraints.required_resource_keys)
        if result.max_images == 0:
            result.input_media_types.discard("image")
        if result.max_videos == 0:
            result.input_media_types.discard("video")
        if result.max_audio == 0:
            result.input_media_types.discard("audio")
        return result

    @classmethod
    def effective_capabilities(
        cls,
        *,
        capability_map: dict[str, dict[str, Any]],
        config_override: dict[str, Any] | None = None,
        strict_catalog: bool = False,
        strict_override: bool = False,
        require_usable: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(capability_map, dict):
            raise _capability_error("capabilities must contain an object")
        if not capability_map:
            if require_usable:
                raise _capability_error("at least one capability mode is required")
            return cls._document({})
        modes = cls._catalog_modes(capability_map, strict=strict_catalog)
        override = config_override or {}
        if not isinstance(override, dict):
            raise _capability_error("company model override must contain an object")
        if override:
            if strict_override or (
                override.get("schema_version") == SCHEMA_VERSION
                and isinstance(override.get("modes"), dict)
            ):
                modes = cls._apply_canonical_override(modes, override)
            else:
                restricted: dict[str, _ModeCapability] = {}
                for mode, capability in modes.items():
                    allowed_modes, constraints = cls._legacy_override_constraints(
                        override, mode=mode
                    )
                    if allowed_modes is not None and mode not in allowed_modes:
                        continue
                    candidate = cls._apply_legacy_constraints(
                        capability, constraints
                    )
                    try:
                        cls._validate_mode_capability(mode, candidate)
                    except ConflictError:
                        continue
                    restricted[mode] = candidate
                modes = restricted
        if require_usable and not modes:
            raise _capability_error("at least one usable capability mode is required")
        return cls._document(modes)

    @classmethod
    def validate_catalog(
        cls,
        capability_map: dict[str, dict[str, Any]],
        *,
        require_usable: bool,
    ) -> dict[str, Any]:
        return cls.effective_capabilities(
            capability_map=capability_map,
            strict_catalog=True,
            require_usable=require_usable,
        )

    @classmethod
    def validate_company_override(
        cls,
        *,
        capability_map: dict[str, dict[str, Any]],
        config_override: dict[str, Any],
    ) -> dict[str, Any]:
        if not config_override:
            return cls.effective_capabilities(
                capability_map=capability_map,
                strict_catalog=False,
                require_usable=True,
            )
        return cls.effective_capabilities(
            capability_map=capability_map,
            config_override=config_override,
            strict_catalog=False,
            strict_override=True,
            require_usable=True,
        )

    @staticmethod
    def _positive_request_int(
        request_payload: dict[str, Any],
        key: str,
        *,
        default: int,
        maximum: int,
    ) -> int:
        value = request_payload.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > maximum
        ):
            raise ConflictError(
                f"{key} must be an integer between 1 and {maximum}"
            )
        return value

    @classmethod
    def _validate_request(
        cls,
        request_payload: dict[str, Any],
        *,
        mode: str,
        effective: dict[str, Any],
    ) -> None:
        unknown = set(request_payload) - set(cls._REQUEST_KEYS)
        if unknown:
            raise ConflictError(
                "request_payload contains unknown fields: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        metadata = request_payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ConflictError("request_payload.metadata must be an object")
        prompt = request_payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ConflictError("prompt must be a non-empty string")
        limits = effective["limits"]
        if len(prompt) > limits["max_prompt_length"]:
            raise ConflictError("prompt exceeds the model capability limit")
        aspect_ratio = request_payload.get("aspect_ratio", "16:9")
        resolution = request_payload.get("resolution", "720p")
        if not isinstance(aspect_ratio, str) or ASPECT_RATIO_PATTERN.fullmatch(
            aspect_ratio
        ) is None:
            raise ConflictError("aspect_ratio is invalid")
        if not isinstance(resolution, str) or RESOLUTION_PATTERN.fullmatch(
            resolution
        ) is None:
            raise ConflictError("resolution is invalid")
        duration = cls._positive_request_int(
            request_payload,
            "duration_seconds",
            default=5,
            maximum=MAX_DURATION_SECONDS,
        )
        output_count = cls._positive_request_int(
            request_payload,
            "output_count",
            default=1,
            maximum=MAX_OUTPUT_COUNT,
        )
        face_enabled = request_payload.get("face_enabled", False)
        if not isinstance(face_enabled, bool):
            raise ConflictError("face_enabled must be a boolean")
        if face_enabled and not effective["supports_face"]:
            raise ConflictError("face input is not allowed by the model capability")
        if aspect_ratio not in limits["aspect_ratios"]:
            raise ConflictError("aspect_ratio is not allowed by the model capability")
        if resolution not in limits["resolutions"]:
            raise ConflictError("resolution is not allowed by the model capability")
        if duration not in limits["duration_seconds"]:
            raise ConflictError(
                "duration_seconds is not allowed by the model capability"
            )
        if output_count not in limits["output_counts"]:
            raise ConflictError("output_count is not allowed by the model capability")

        raw_assets = request_payload.get("assets", [])
        if not isinstance(raw_assets, list):
            raise ConflictError("request_payload.assets must be a list")
        if len(raw_assets) > MAX_TOTAL_ASSETS:
            raise ConflictError(
                f"request_payload.assets supports at most {MAX_TOTAL_ASSETS} items"
            )
        counts = {"image": 0, "video": 0, "audio": 0}
        for asset in raw_assets:
            if not isinstance(asset, dict):
                raise ConflictError("Input asset reference is invalid")
            media_type = asset.get("media_type")
            if media_type not in counts:
                raise ConflictError("Input asset media_type is invalid")
            counts[media_type] += 1
        for media_type, maximum in (
            ("image", limits["max_images"]),
            ("video", limits["max_videos"]),
            ("audio", limits["max_audio"]),
        ):
            if counts[media_type] and media_type not in effective["input_media_types"]:
                raise ConflictError(
                    f"{media_type} inputs are not allowed by the model capability"
                )
            if counts[media_type] > maximum:
                raise ConflictError(
                    f"{media_type} input count exceeds the model capability"
                )
        if mode == "image_to_video" and counts["image"] == 0:
            raise ConflictError("image_to_video requires at least one image input")
        if mode == "video_to_video" and counts["video"] == 0:
            raise ConflictError("video_to_video requires at least one video input")

    @classmethod
    def validate(
        cls,
        *,
        capability_map: dict[str, dict[str, Any]],
        config_override: dict[str, Any],
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(request_payload, dict):
            raise ConflictError("request_payload must be an object")
        raw_mode = request_payload.get("mode", "text_to_video")
        if not isinstance(raw_mode, str) or raw_mode not in SUPPORTED_MODES:
            raise ConflictError("mode must be a supported generation mode")
        document = cls.effective_capabilities(
            capability_map=capability_map,
            config_override=config_override,
            strict_catalog=False,
            strict_override=False,
            require_usable=True,
        )
        mode_config = document["modes"].get(raw_mode)
        if mode_config is None:
            raise ConflictError("mode is not allowed by the model capability")
        cls._validate_request(
            request_payload,
            mode=raw_mode,
            effective=mode_config,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "modes": {raw_mode: mode_config},
        }
