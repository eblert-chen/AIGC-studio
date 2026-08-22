from __future__ import annotations

import os
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from .callback import (
    MINIMUM_CALLBACK_SECRET_BYTES,
    CallbackConfigurationError,
    CallbackRoute,
    callback_secret_is_placeholder,
    normalize_callback_url,
    parse_callback_routes,
)


@dataclass(frozen=True)
class RelaySettings:
    environment: Literal["development", "production"] = "development"
    runtime_mode: Literal["memory", "production"] = "memory"
    database_url: str | None = None
    redis_url: str | None = None
    worker_max_attempts: int = 3
    submission_claim_lease_seconds: float = 120.0
    enable_mock_provider: bool = False
    provider_factories: tuple[str, ...] = ()
    provider_healthcheck_timeout_seconds: float = 5.0
    provider_failure_threshold: int = 3
    provider_cooldown_seconds: float = 30.0
    provider_admission_retry_seconds: float = 5.0
    artifact_store: Literal["memory", "filesystem", "huawei_obs"] = "memory"
    artifact_filesystem_root: str | None = None
    artifact_public_base_url: str | None = None
    artifact_signing_secret: str | None = None
    artifact_max_bytes: int = 512 * 1024 * 1024
    artifact_timeout_seconds: float = 60
    transfer_max_attempts: int = 3
    artifact_transfer_claim_lease_seconds: float = 120.0
    callback_routes: dict[UUID, CallbackRoute] = field(default_factory=dict)
    callback_max_attempts: int = 8
    callback_base_delay_seconds: float = 5
    callback_max_delay_seconds: float = 3600
    callback_timeout_seconds: float = 10
    callback_poll_seconds: float = 0.5
    provider_poll_seconds: float = 15
    provider_poll_batch_size: int = 100
    provider_poll_concurrency: int = 8
    provider_poll_claim_lease_seconds: float = 120.0
    provider_poll_error_base_seconds: float = 15
    provider_poll_error_max_seconds: float = 300
    provider_monitor_enabled: bool = True
    provider_monitor_interval_seconds: float = 30
    provider_monitor_window_seconds: float = 300
    provider_monitor_min_outcomes: int = 20
    provider_monitor_min_success_rate: float = 0.80
    provider_monitor_widespread_failure_ratio: float = 0.50
    provider_monitor_widespread_min_routes: int = 2
    provider_monitor_batch_disabled_threshold: int = 3
    provider_monitor_breach_cycles: int = 2
    provider_monitor_recovery_cycles: int = 2
    provider_monitor_lease_seconds: float = 120
    provider_monitor_retention_days: int = 30
    provider_monitor_retired_providers: tuple[str, ...] = ()
    provider_alert_webhook_url: str | None = None
    provider_alert_signing_secret: str | None = None
    provider_alert_timeout_seconds: float = 5
    provider_alert_max_attempts: int = 8
    provider_alert_claim_lease_seconds: float = 60
    provider_alert_base_delay_seconds: float = 5
    provider_alert_max_delay_seconds: float = 900
    provider_alert_poll_seconds: float = 0.5

    @classmethod
    def from_environment(cls) -> "RelaySettings":
        mode = os.getenv("RELAY_RUNTIME_MODE", "memory").lower()
        if mode not in {"memory", "production"}:
            raise RuntimeError("RELAY_RUNTIME_MODE must be 'memory' or 'production'")
        environment = os.getenv("RELAY_ENVIRONMENT", "development").lower()
        settings = cls(
            environment=environment,  # type: ignore[arg-type]
            runtime_mode=mode,  # type: ignore[arg-type]
            database_url=os.getenv("RELAY_DATABASE_URL"),
            redis_url=os.getenv("RELAY_REDIS_URL"),
            worker_max_attempts=int(os.getenv("RELAY_WORKER_MAX_ATTEMPTS", "3")),
            submission_claim_lease_seconds=float(
                os.getenv("RELAY_SUBMISSION_CLAIM_LEASE_SECONDS", "120")
            ),
            enable_mock_provider=os.getenv("RELAY_ENABLE_MOCK_PROVIDER", "").lower()
            in {"1", "true", "yes"},
            provider_factories=tuple(
                item.strip()
                for item in os.getenv("RELAY_PROVIDER_FACTORIES", "").split(",")
                if item.strip()
            ),
            provider_healthcheck_timeout_seconds=float(
                os.getenv("RELAY_PROVIDER_HEALTHCHECK_TIMEOUT_SECONDS", "5")
            ),
            provider_failure_threshold=int(
                os.getenv("RELAY_PROVIDER_FAILURE_THRESHOLD", "3")
            ),
            provider_cooldown_seconds=float(
                os.getenv("RELAY_PROVIDER_COOLDOWN_SECONDS", "30")
            ),
            provider_admission_retry_seconds=float(
                os.getenv("RELAY_PROVIDER_ADMISSION_RETRY_SECONDS", "5")
            ),
            artifact_store=os.getenv(
                "RELAY_ARTIFACT_STORE", "memory"
            ).lower(),  # type: ignore[arg-type]
            artifact_filesystem_root=os.getenv("RELAY_ARTIFACT_FILESYSTEM_ROOT"),
            artifact_public_base_url=os.getenv("RELAY_ARTIFACT_PUBLIC_BASE_URL"),
            artifact_signing_secret=os.getenv("RELAY_ARTIFACT_SIGNING_SECRET"),
            artifact_max_bytes=int(
                os.getenv("RELAY_ARTIFACT_MAX_BYTES", str(512 * 1024 * 1024))
            ),
            artifact_timeout_seconds=float(
                os.getenv("RELAY_ARTIFACT_TIMEOUT_SECONDS", "60")
            ),
            transfer_max_attempts=int(os.getenv("RELAY_TRANSFER_MAX_ATTEMPTS", "3")),
            artifact_transfer_claim_lease_seconds=float(
                os.getenv("RELAY_ARTIFACT_TRANSFER_CLAIM_LEASE_SECONDS", "120")
            ),
            callback_routes=parse_callback_routes(
                os.getenv("RELAY_CALLBACK_ROUTES_JSON"),
                production=environment == "production",
            ),
            callback_max_attempts=int(os.getenv("RELAY_CALLBACK_MAX_ATTEMPTS", "8")),
            callback_base_delay_seconds=float(
                os.getenv("RELAY_CALLBACK_BASE_DELAY_SECONDS", "5")
            ),
            callback_max_delay_seconds=float(
                os.getenv("RELAY_CALLBACK_MAX_DELAY_SECONDS", "3600")
            ),
            callback_timeout_seconds=float(
                os.getenv("RELAY_CALLBACK_TIMEOUT_SECONDS", "10")
            ),
            callback_poll_seconds=float(
                os.getenv("RELAY_CALLBACK_POLL_SECONDS", "0.5")
            ),
            provider_poll_seconds=float(os.getenv("RELAY_PROVIDER_POLL_SECONDS", "15")),
            provider_poll_batch_size=int(
                os.getenv("RELAY_PROVIDER_POLL_BATCH_SIZE", "100")
            ),
            provider_poll_concurrency=int(
                os.getenv("RELAY_PROVIDER_POLL_CONCURRENCY", "8")
            ),
            provider_poll_claim_lease_seconds=float(
                os.getenv("RELAY_PROVIDER_POLL_CLAIM_LEASE_SECONDS", "120")
            ),
            provider_poll_error_base_seconds=float(
                os.getenv("RELAY_PROVIDER_POLL_ERROR_BASE_SECONDS", "15")
            ),
            provider_poll_error_max_seconds=float(
                os.getenv("RELAY_PROVIDER_POLL_ERROR_MAX_SECONDS", "300")
            ),
            provider_monitor_enabled=os.getenv(
                "RELAY_PROVIDER_MONITOR_ENABLED", "true"
            ).lower()
            in {"1", "true", "yes"},
            provider_monitor_interval_seconds=float(
                os.getenv("RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS", "30")
            ),
            provider_monitor_window_seconds=float(
                os.getenv("RELAY_PROVIDER_MONITOR_WINDOW_SECONDS", "300")
            ),
            provider_monitor_min_outcomes=int(
                os.getenv("RELAY_PROVIDER_MONITOR_MIN_OUTCOMES", "20")
            ),
            provider_monitor_min_success_rate=float(
                os.getenv("RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE", "0.80")
            ),
            provider_monitor_widespread_failure_ratio=float(
                os.getenv(
                    "RELAY_PROVIDER_MONITOR_WIDESPREAD_FAILURE_RATIO", "0.50"
                )
            ),
            provider_monitor_widespread_min_routes=int(
                os.getenv(
                    "RELAY_PROVIDER_MONITOR_WIDESPREAD_MIN_ROUTES", "2"
                )
            ),
            provider_monitor_batch_disabled_threshold=int(
                os.getenv(
                    "RELAY_PROVIDER_MONITOR_BATCH_DISABLED_THRESHOLD", "3"
                )
            ),
            provider_monitor_breach_cycles=int(
                os.getenv("RELAY_PROVIDER_MONITOR_BREACH_CYCLES", "2")
            ),
            provider_monitor_recovery_cycles=int(
                os.getenv("RELAY_PROVIDER_MONITOR_RECOVERY_CYCLES", "2")
            ),
            provider_monitor_lease_seconds=float(
                os.getenv("RELAY_PROVIDER_MONITOR_LEASE_SECONDS", "120")
            ),
            provider_monitor_retention_days=int(
                os.getenv("RELAY_PROVIDER_MONITOR_RETENTION_DAYS", "30")
            ),
            provider_monitor_retired_providers=tuple(
                item.strip()
                for item in os.getenv(
                    "RELAY_PROVIDER_MONITOR_RETIRED_PROVIDERS", ""
                ).split(",")
                if item.strip()
            ),
            provider_alert_webhook_url=(
                os.getenv("RELAY_PROVIDER_ALERT_WEBHOOK_URL") or None
            ),
            provider_alert_signing_secret=(
                os.getenv("RELAY_PROVIDER_ALERT_SIGNING_SECRET") or None
            ),
            provider_alert_timeout_seconds=float(
                os.getenv("RELAY_PROVIDER_ALERT_TIMEOUT_SECONDS", "5")
            ),
            provider_alert_max_attempts=int(
                os.getenv("RELAY_PROVIDER_ALERT_MAX_ATTEMPTS", "8")
            ),
            provider_alert_claim_lease_seconds=float(
                os.getenv("RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS", "60")
            ),
            provider_alert_base_delay_seconds=float(
                os.getenv("RELAY_PROVIDER_ALERT_BASE_DELAY_SECONDS", "5")
            ),
            provider_alert_max_delay_seconds=float(
                os.getenv("RELAY_PROVIDER_ALERT_MAX_DELAY_SECONDS", "900")
            ),
            provider_alert_poll_seconds=float(
                os.getenv("RELAY_PROVIDER_ALERT_POLL_SECONDS", "0.5")
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "production"}:
            raise RuntimeError(
                "RELAY_ENVIRONMENT must be 'development' or 'production'"
            )
        finite_fields = (
            ("RELAY_SUBMISSION_CLAIM_LEASE_SECONDS", self.submission_claim_lease_seconds),
            (
                "RELAY_PROVIDER_HEALTHCHECK_TIMEOUT_SECONDS",
                self.provider_healthcheck_timeout_seconds,
            ),
            ("RELAY_PROVIDER_COOLDOWN_SECONDS", self.provider_cooldown_seconds),
            (
                "RELAY_PROVIDER_ADMISSION_RETRY_SECONDS",
                self.provider_admission_retry_seconds,
            ),
            ("RELAY_ARTIFACT_TIMEOUT_SECONDS", self.artifact_timeout_seconds),
            (
                "RELAY_ARTIFACT_TRANSFER_CLAIM_LEASE_SECONDS",
                self.artifact_transfer_claim_lease_seconds,
            ),
            ("RELAY_CALLBACK_BASE_DELAY_SECONDS", self.callback_base_delay_seconds),
            ("RELAY_CALLBACK_MAX_DELAY_SECONDS", self.callback_max_delay_seconds),
            ("RELAY_CALLBACK_TIMEOUT_SECONDS", self.callback_timeout_seconds),
            ("RELAY_CALLBACK_POLL_SECONDS", self.callback_poll_seconds),
            ("RELAY_PROVIDER_POLL_SECONDS", self.provider_poll_seconds),
            (
                "RELAY_PROVIDER_POLL_CLAIM_LEASE_SECONDS",
                self.provider_poll_claim_lease_seconds,
            ),
            (
                "RELAY_PROVIDER_POLL_ERROR_BASE_SECONDS",
                self.provider_poll_error_base_seconds,
            ),
            (
                "RELAY_PROVIDER_POLL_ERROR_MAX_SECONDS",
                self.provider_poll_error_max_seconds,
            ),
            (
                "RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS",
                self.provider_monitor_interval_seconds,
            ),
            (
                "RELAY_PROVIDER_MONITOR_WINDOW_SECONDS",
                self.provider_monitor_window_seconds,
            ),
            (
                "RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE",
                self.provider_monitor_min_success_rate,
            ),
            (
                "RELAY_PROVIDER_MONITOR_WIDESPREAD_FAILURE_RATIO",
                self.provider_monitor_widespread_failure_ratio,
            ),
            (
                "RELAY_PROVIDER_MONITOR_LEASE_SECONDS",
                self.provider_monitor_lease_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_TIMEOUT_SECONDS",
                self.provider_alert_timeout_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS",
                self.provider_alert_claim_lease_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_BASE_DELAY_SECONDS",
                self.provider_alert_base_delay_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_MAX_DELAY_SECONDS",
                self.provider_alert_max_delay_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_POLL_SECONDS",
                self.provider_alert_poll_seconds,
            ),
        )
        for name, value in finite_fields:
            if not math.isfinite(value):
                raise RuntimeError(f"{name} must be finite")
        if self.worker_max_attempts < 1:
            raise RuntimeError("RELAY_WORKER_MAX_ATTEMPTS must be at least 1")
        if self.submission_claim_lease_seconds <= 0:
            raise RuntimeError("RELAY_SUBMISSION_CLAIM_LEASE_SECONDS must be positive")
        if self.artifact_store not in {
            "memory",
            "filesystem",
            "huawei_obs",
        }:
            raise RuntimeError(
                "RELAY_ARTIFACT_STORE must be 'memory', 'filesystem', "
                "or 'huawei_obs'"
            )
        if self.artifact_store == "filesystem":
            if self.environment != "development":
                raise RuntimeError("Filesystem artifact storage is development-only")
            missing = [
                name
                for name, value in (
                    (
                        "RELAY_ARTIFACT_FILESYSTEM_ROOT",
                        self.artifact_filesystem_root,
                    ),
                    (
                        "RELAY_ARTIFACT_PUBLIC_BASE_URL",
                        self.artifact_public_base_url,
                    ),
                    (
                        "RELAY_ARTIFACT_SIGNING_SECRET",
                        self.artifact_signing_secret,
                    ),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "Filesystem artifact configuration is incomplete: "
                    + ", ".join(missing)
                )
            if not Path(self.artifact_filesystem_root or "").is_absolute():
                raise RuntimeError(
                    "RELAY_ARTIFACT_FILESYSTEM_ROOT must be an absolute path"
                )
            parsed_base = urlsplit(self.artifact_public_base_url or "")
            if (
                parsed_base.scheme not in {"http", "https"}
                or not parsed_base.hostname
                or parsed_base.username
                or parsed_base.password
                or parsed_base.query
                or parsed_base.fragment
            ):
                raise RuntimeError(
                    "RELAY_ARTIFACT_PUBLIC_BASE_URL must be a "
                    "credential-free HTTP(S) URL"
                )
            if len((self.artifact_signing_secret or "").encode("utf-8")) < 32:
                raise RuntimeError(
                    "RELAY_ARTIFACT_SIGNING_SECRET must contain at least " "32 bytes"
                )
        if self.artifact_max_bytes < 1:
            raise RuntimeError("RELAY_ARTIFACT_MAX_BYTES must be positive")
        if self.artifact_timeout_seconds <= 0:
            raise RuntimeError("RELAY_ARTIFACT_TIMEOUT_SECONDS must be positive")
        if self.transfer_max_attempts < 1:
            raise RuntimeError("RELAY_TRANSFER_MAX_ATTEMPTS must be at least 1")
        if self.artifact_transfer_claim_lease_seconds <= 0:
            raise RuntimeError(
                "RELAY_ARTIFACT_TRANSFER_CLAIM_LEASE_SECONDS must be positive"
            )
        if self.callback_max_attempts < 1:
            raise RuntimeError("RELAY_CALLBACK_MAX_ATTEMPTS must be at least 1")
        if self.callback_base_delay_seconds <= 0:
            raise RuntimeError("RELAY_CALLBACK_BASE_DELAY_SECONDS must be positive")
        if self.callback_max_delay_seconds <= 0:
            raise RuntimeError("RELAY_CALLBACK_MAX_DELAY_SECONDS must be positive")
        if self.callback_timeout_seconds <= 0:
            raise RuntimeError("RELAY_CALLBACK_TIMEOUT_SECONDS must be positive")
        if self.callback_poll_seconds <= 0:
            raise RuntimeError("RELAY_CALLBACK_POLL_SECONDS must be positive")
        if self.provider_poll_seconds <= 0:
            raise RuntimeError("RELAY_PROVIDER_POLL_SECONDS must be positive")
        if self.provider_healthcheck_timeout_seconds <= 0:
            raise RuntimeError(
                "RELAY_PROVIDER_HEALTHCHECK_TIMEOUT_SECONDS must be positive"
            )
        if self.provider_failure_threshold < 1:
            raise RuntimeError(
                "RELAY_PROVIDER_FAILURE_THRESHOLD must be at least 1"
            )
        if self.provider_cooldown_seconds < 0:
            raise RuntimeError(
                "RELAY_PROVIDER_COOLDOWN_SECONDS must not be negative"
            )
        if self.provider_admission_retry_seconds <= 0:
            raise RuntimeError(
                "RELAY_PROVIDER_ADMISSION_RETRY_SECONDS must be positive"
            )
        if self.provider_poll_batch_size < 1:
            raise RuntimeError("RELAY_PROVIDER_POLL_BATCH_SIZE must be at least 1")
        if self.provider_poll_concurrency < 1:
            raise RuntimeError("RELAY_PROVIDER_POLL_CONCURRENCY must be at least 1")
        if self.provider_poll_claim_lease_seconds <= 0:
            raise RuntimeError(
                "RELAY_PROVIDER_POLL_CLAIM_LEASE_SECONDS must be positive"
            )
        if self.provider_poll_error_base_seconds <= 0:
            raise RuntimeError(
                "RELAY_PROVIDER_POLL_ERROR_BASE_SECONDS must be positive"
            )
        if self.provider_poll_error_max_seconds < self.provider_poll_error_base_seconds:
            raise RuntimeError(
                "RELAY_PROVIDER_POLL_ERROR_MAX_SECONDS must not be below "
                "RELAY_PROVIDER_POLL_ERROR_BASE_SECONDS"
            )
        if self.provider_monitor_interval_seconds <= 0:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS must be positive"
            )
        if self.provider_monitor_window_seconds <= 0:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_WINDOW_SECONDS must be positive"
            )
        if self.provider_monitor_min_outcomes < 1:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_MIN_OUTCOMES must be at least 1"
            )
        if not 0 <= self.provider_monitor_min_success_rate <= 1:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE must be between 0 and 1"
            )
        if not 0 <= self.provider_monitor_widespread_failure_ratio <= 1:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_WIDESPREAD_FAILURE_RATIO must be "
                "between 0 and 1"
            )
        if self.provider_monitor_widespread_min_routes < 2:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_WIDESPREAD_MIN_ROUTES must be at least 2"
            )
        for name, value in (
            (
                "RELAY_PROVIDER_MONITOR_BATCH_DISABLED_THRESHOLD",
                self.provider_monitor_batch_disabled_threshold,
            ),
            (
                "RELAY_PROVIDER_MONITOR_BREACH_CYCLES",
                self.provider_monitor_breach_cycles,
            ),
            (
                "RELAY_PROVIDER_MONITOR_RECOVERY_CYCLES",
                self.provider_monitor_recovery_cycles,
            ),
            (
                "RELAY_PROVIDER_MONITOR_RETENTION_DAYS",
                self.provider_monitor_retention_days,
            ),
            (
                "RELAY_PROVIDER_ALERT_MAX_ATTEMPTS",
                self.provider_alert_max_attempts,
            ),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be at least 1")
        if len(set(self.provider_monitor_retired_providers)) != len(
            self.provider_monitor_retired_providers
        ):
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_RETIRED_PROVIDERS contains duplicates"
            )
        for provider_name in self.provider_monitor_retired_providers:
            if (
                len(provider_name) > 64
                or re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]*", provider_name
                )
                is None
            ):
                raise RuntimeError(
                    "RELAY_PROVIDER_MONITOR_RETIRED_PROVIDERS contains an "
                    "invalid provider identifier"
                )
        if self.provider_monitor_lease_seconds <= self.provider_healthcheck_timeout_seconds:
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_LEASE_SECONDS must exceed the provider "
                "healthcheck timeout"
            )
        if (
            self.provider_monitor_lease_seconds
            < self.provider_monitor_interval_seconds
        ):
            raise RuntimeError(
                "RELAY_PROVIDER_MONITOR_LEASE_SECONDS must not be shorter than "
                "the monitor interval"
            )
        for name, value in (
            (
                "RELAY_PROVIDER_ALERT_TIMEOUT_SECONDS",
                self.provider_alert_timeout_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_BASE_DELAY_SECONDS",
                self.provider_alert_base_delay_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_MAX_DELAY_SECONDS",
                self.provider_alert_max_delay_seconds,
            ),
            (
                "RELAY_PROVIDER_ALERT_POLL_SECONDS",
                self.provider_alert_poll_seconds,
            ),
        ):
            if value <= 0:
                raise RuntimeError(f"{name} must be positive")
        if self.provider_alert_max_delay_seconds < self.provider_alert_base_delay_seconds:
            raise RuntimeError(
                "RELAY_PROVIDER_ALERT_MAX_DELAY_SECONDS must not be below the "
                "base delay"
            )
        if self.provider_alert_claim_lease_seconds <= self.provider_alert_timeout_seconds:
            raise RuntimeError(
                "RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS must exceed the "
                "end-to-end alert timeout"
            )
        if bool(self.provider_alert_webhook_url) != bool(
            self.provider_alert_signing_secret
        ):
            raise RuntimeError(
                "RELAY_PROVIDER_ALERT_WEBHOOK_URL and "
                "RELAY_PROVIDER_ALERT_SIGNING_SECRET must be configured together"
            )
        if self.provider_alert_webhook_url is not None:
            if len((self.provider_alert_signing_secret or "").encode("utf-8")) < 32:
                raise RuntimeError(
                    "RELAY_PROVIDER_ALERT_SIGNING_SECRET must contain at least "
                    "32 bytes"
                )
            if self.environment == "production" and callback_secret_is_placeholder(
                self.provider_alert_signing_secret or ""
            ):
                raise RuntimeError(
                    "RELAY_PROVIDER_ALERT_SIGNING_SECRET uses an obvious placeholder"
                )
            try:
                normalized_alert_url = normalize_callback_url(
                    self.provider_alert_webhook_url,
                    production=self.environment == "production",
                )
            except CallbackConfigurationError as exc:
                raise RuntimeError(
                    "RELAY_PROVIDER_ALERT_WEBHOOK_URL is not allowed"
                ) from exc
            if normalized_alert_url != self.provider_alert_webhook_url:
                raise RuntimeError(
                    "RELAY_PROVIDER_ALERT_WEBHOOK_URL must be normalized"
                )
        for tenant_id, route in self.callback_routes.items():
            if not isinstance(tenant_id, UUID):
                raise RuntimeError("Callback route keys must be tenant UUIDs")
            if (
                not isinstance(route, CallbackRoute)
                or len(route.signing_secret.encode("utf-8"))
                < MINIMUM_CALLBACK_SECRET_BYTES
            ):
                raise RuntimeError(
                    f"Callback signing secret for tenant '{tenant_id}' must "
                    f"contain at least {MINIMUM_CALLBACK_SECRET_BYTES} bytes"
                )
            if self.environment == "production" and callback_secret_is_placeholder(
                route.signing_secret
            ):
                raise RuntimeError(
                    f"Callback signing secret for tenant '{tenant_id}' uses "
                    "an obvious placeholder"
                )
            try:
                normalized = normalize_callback_url(
                    route.url,
                    production=self.environment == "production",
                )
            except CallbackConfigurationError as exc:
                raise RuntimeError(
                    f"Callback URL for tenant '{tenant_id}' is not allowed: " f"{exc}"
                ) from exc
            if normalized != route.url:
                raise RuntimeError(
                    f"Callback URL for tenant '{tenant_id}' must be normalized"
                )
        if self.runtime_mode == "production":
            if not self.database_url:
                raise RuntimeError("RELAY_DATABASE_URL is required in production mode")
            if not self.redis_url:
                raise RuntimeError("RELAY_REDIS_URL is required in production mode")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise RuntimeError(
                    "Persistent Relay requires postgresql+asyncpg:// for "
                    "RELAY_DATABASE_URL"
                )
        if self.environment == "production":
            if self.runtime_mode != "production":
                raise RuntimeError(
                    "Production environment requires persistent runtime mode"
                )
            if self.artifact_store != "huawei_obs":
                raise RuntimeError(
                    "Production environment requires " "RELAY_ARTIFACT_STORE=huawei_obs"
                )
            if self.enable_mock_provider:
                raise RuntimeError(
                    "Mock Provider cannot be enabled in production environment"
                )
            if not self.provider_monitor_enabled:
                raise RuntimeError(
                    "Production environment requires RELAY_PROVIDER_MONITOR_ENABLED=true"
                )
            if self.provider_alert_webhook_url is None:
                raise RuntimeError(
                    "Production environment requires a provider alert webhook and "
                    "signing secret"
                )
