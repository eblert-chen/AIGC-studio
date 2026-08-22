from __future__ import annotations

from collections import Counter, defaultdict
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import ceil
from typing import Any, Iterable, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    ChannelCostEntry,
    Company,
    CompanyModelGrant,
    CompanyResourceGrant,
    DownloadGatewayRegistrationAttempt,
    DownloadGatewayRegistrationStatus,
    DownloadCompletion,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelDefinition,
    PublicationJob,
    PublicationJobStatus,
    PublisherConnection,
    PublisherConnectionStatus,
    RelayOutboxStatus,
    RelayCallbackEvent,
    RelaySubmissionOutbox,
    RelayOperationsSnapshot,
    RelayRouteOperationsSnapshot,
    RelayTaskStage,
    RelayTaskStageEvent,
    ResourceDefinition,
    TaskStatus,
    TaskArtifact,
    TaskTimeoutEvent,
    WalletAccount,
)
from .errors import ConflictError


Granularity = Literal["day", "week", "month"]


@dataclass(frozen=True)
class AnalyticsWindow:
    start: datetime
    end: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_window(
    *, start: datetime, end: datetime, max_days: int = 366
) -> AnalyticsWindow:
    normalized_start = _utc(start)
    normalized_end = _utc(end)
    if normalized_start >= normalized_end:
        raise ConflictError("start_time must be before end_time")
    if normalized_end - normalized_start > timedelta(days=max_days):
        raise ConflictError(f"analytics window cannot exceed {max_days} days")
    return AnalyticsWindow(start=normalized_start, end=normalized_end)


def _bucket_start(value: datetime, granularity: Granularity) -> date:
    current = _utc(value).date()
    if granularity == "day":
        return current
    if granularity == "week":
        return current - timedelta(days=current.weekday())
    return current.replace(day=1)


def _next_bucket(current: date, granularity: Granularity) -> date:
    if granularity == "day":
        return current + timedelta(days=1)
    if granularity == "week":
        return current + timedelta(days=7)
    if current.month == 12:
        return date(current.year + 1, 1, 1)
    return date(current.year, current.month + 1, 1)


def _bucket_end(current: date, granularity: Granularity) -> datetime:
    next_date = _next_bucket(current, granularity)
    return datetime.combine(next_date, time.min, tzinfo=timezone.utc)


def _bucket_sequence(window: AnalyticsWindow, granularity: Granularity) -> list[date]:
    current = _bucket_start(window.start, granularity)
    result: list[date] = []
    while datetime.combine(current, time.min, tzinfo=timezone.utc) < window.end:
        result.append(current)
        current = _next_bucket(current, granularity)
    return result


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    # Nearest-rank is deterministic across SQLite and PostgreSQL and does not
    # interpolate a latency that was never observed.
    rank = max(1, ceil(percentile * len(ordered)))
    return round(ordered[rank - 1], 3)


def _duration_seconds(created_at: datetime, updated_at: datetime) -> float:
    return max(0.0, (_utc(updated_at) - _utc(created_at)).total_seconds())


def _shift_year(value: datetime, years: int = -1) -> datetime:
    target_year = value.year + years
    target_day = min(value.day, monthrange(target_year, value.month)[1])
    return value.replace(year=target_year, day=target_day)


def _comparison_metric(
    current: int | float | None,
    baseline: int | float | None,
    *,
    baseline_available: bool,
) -> dict[str, int | float | None]:
    if not baseline_available:
        return {
            "current": current,
            "baseline": None,
            "absolute_change": None,
            "change_rate": None,
        }
    if current is None or baseline is None:
        return {
            "current": current,
            "baseline": baseline,
            "absolute_change": None,
            "change_rate": None,
        }
    difference = current - baseline
    if isinstance(difference, float):
        difference = round(difference, 6)
    return {
        "current": current,
        "baseline": baseline,
        "absolute_change": difference,
        "change_rate": (
            round(difference / baseline, 6) if baseline != 0 else None
        ),
    }


def _task_trend_granularity(window: AnalyticsWindow) -> Granularity:
    duration = window.end - window.start
    if duration <= timedelta(days=31):
        return "day"
    if duration <= timedelta(days=180):
        return "week"
    return "month"


def _latency_distribution(values: Iterable[float]) -> dict[str, Any]:
    samples = [float(value) for value in values]
    if not samples:
        return {"sample_count": 0, "bins": []}
    definitions: list[tuple[str, float | None]] = [
        ("[0,10]", 10),
        ("(10,30]", 30),
        ("(30,60]", 60),
        ("(60,120]", 120),
        ("(120,300]", 300),
        ("(300,600]", 600),
        ("(600,+inf)", None),
    ]
    counts = [0 for _ in definitions]
    for sample in samples:
        for index, (_, upper_bound) in enumerate(definitions):
            if upper_bound is None or sample <= upper_bound:
                counts[index] += 1
                break
    return {
        "sample_count": len(samples),
        "bins": [
            {"range": label, "count": counts[index]}
            for index, (label, _) in enumerate(definitions)
        ],
    }


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _safe_error_code(task: GenerationTask) -> str:
    snapshot = task.relay_error_snapshot
    if isinstance(snapshot, dict):
        direct = snapshot.get("code")
        if isinstance(direct, str) and direct:
            return direct[:160]
        nested = snapshot.get("error")
        if isinstance(nested, dict):
            code = nested.get("code")
            if isinstance(code, str) and code:
                return code[:160]
    return "UNCLASSIFIED_FAILURE"


def _safe_route_id(task: GenerationTask) -> str | None:
    snapshot = task.relay_error_snapshot
    if not isinstance(snapshot, dict):
        return None
    candidates: list[Any] = [snapshot.get("route_id"), snapshot.get("channel_key")]
    details = snapshot.get("details")
    if isinstance(details, dict):
        candidates.extend((details.get("route_id"), details.get("channel_key")))
    for candidate in candidates:
        if isinstance(candidate, str) and 0 < len(candidate) <= 120:
            return candidate
    return None


class AdminAnalyticsService:
    @staticmethod
    def operating_series(
        session: Session,
        *,
        start: datetime,
        end: datetime,
        granularity: Granularity,
        _include_comparisons: bool = True,
    ) -> dict[str, Any]:
        # A leap-day clamp can make the prior-calendar-year comparison one day
        # longer than the public 366-day maximum. Only the private baseline
        # calculation receives that narrow allowance.
        window = validate_window(
            start=start,
            end=end,
            max_days=367 if not _include_comparisons else 366,
        )
        buckets = {
            key: {
                "bucket_start": key.isoformat(),
                "bucket_end": min(_bucket_end(key, granularity), window.end).isoformat(),
                "recharge_cents": 0,
                "settled_revenue_cents": 0,
                "provider_cost_cents": 0,
                "known_gross_profit_cents": 0,
                "gross_profit_cents": 0,
                "gross_margin": None,
                "cost_missing_task_count": 0,
                "cost_reconciliation_status": "complete",
            }
            for key in _bucket_sequence(window, granularity)
        }

        ledger_rows = session.execute(
            select(LedgerEntry.kind, LedgerEntry.amount_cents, LedgerEntry.created_at).where(
                LedgerEntry.kind.in_((LedgerKind.RECHARGE, LedgerKind.SETTLE)),
                LedgerEntry.created_at >= window.start,
                LedgerEntry.created_at < window.end,
            )
        ).all()
        for kind, amount_cents, occurred_at in ledger_rows:
            row = buckets[_bucket_start(occurred_at, granularity)]
            if kind == LedgerKind.RECHARGE:
                row["recharge_cents"] += int(amount_cents)
            else:
                row["settled_revenue_cents"] += int(amount_cents)

        cost_rows = session.execute(
            select(ChannelCostEntry.amount_cents, ChannelCostEntry.occurred_at).where(
                ChannelCostEntry.company_id.is_not(None),
                ChannelCostEntry.personal_workspace_id.is_(None),
                ChannelCostEntry.occurred_at >= window.start,
                ChannelCostEntry.occurred_at < window.end,
            )
        ).all()
        for amount_cents, occurred_at in cost_rows:
            buckets[_bucket_start(occurred_at, granularity)][
                "provider_cost_cents"
            ] += int(amount_cents)

        missing_cost_rows = session.execute(
            select(GenerationTask.updated_at).where(
                GenerationTask.status == TaskStatus.SUCCEEDED,
                GenerationTask.company_id.is_not(None),
                GenerationTask.personal_workspace_id.is_(None),
                GenerationTask.updated_at >= window.start,
                GenerationTask.updated_at < window.end,
                ~select(ChannelCostEntry.id)
                .where(ChannelCostEntry.task_id == GenerationTask.id)
                .exists(),
            )
        ).all()
        for (occurred_at,) in missing_cost_rows:
            buckets[_bucket_start(occurred_at, granularity)][
                "cost_missing_task_count"
            ] += 1

        totals = {
            "recharge_cents": 0,
            "settled_revenue_cents": 0,
            "provider_cost_cents": 0,
            "known_gross_profit_cents": 0,
            "gross_profit_cents": 0,
            "gross_margin": None,
            "cost_missing_task_count": 0,
            "cost_reconciliation_status": "complete",
        }
        for row in buckets.values():
            revenue = int(row["settled_revenue_cents"])
            cost = int(row["provider_cost_cents"])
            known_profit = revenue - cost
            missing = int(row["cost_missing_task_count"])
            row["known_gross_profit_cents"] = known_profit
            row["gross_profit_cents"] = known_profit if missing == 0 else None
            row["gross_margin"] = (
                round(known_profit / revenue, 6) if missing == 0 and revenue else None
            )
            row["cost_reconciliation_status"] = (
                "complete" if missing == 0 else "incomplete"
            )
            for key in (
                "recharge_cents",
                "settled_revenue_cents",
                "provider_cost_cents",
                "known_gross_profit_cents",
                "cost_missing_task_count",
            ):
                totals[key] += int(row[key])

        total_missing = int(totals["cost_missing_task_count"])
        totals["gross_profit_cents"] = (
            totals["known_gross_profit_cents"] if total_missing == 0 else None
        )
        totals["gross_margin"] = (
            round(
                int(totals["known_gross_profit_cents"])
                / int(totals["settled_revenue_cents"]),
                6,
            )
            if total_missing == 0 and totals["settled_revenue_cents"]
            else None
        )
        totals["cost_reconciliation_status"] = (
            "complete" if total_missing == 0 else "incomplete"
        )
        result: dict[str, Any] = {
            "start_time": window.start.isoformat(),
            "end_time": window.end.isoformat(),
            "granularity": granularity,
            "timezone": "UTC",
            "data_status": (
                "available"
                if ledger_rows or cost_rows or missing_cost_rows
                else "empty"
            ),
            "totals": totals,
            "points": list(buckets.values()),
        }
        if not _include_comparisons:
            return result

        duration = window.end - window.start
        comparison_windows = {
            "period_over_period": (
                window.start - duration,
                window.start,
            ),
            "year_over_year": (
                _shift_year(window.start),
                _shift_year(window.end),
            ),
        }
        comparisons: dict[str, Any] = {}
        metric_names = (
            "recharge_cents",
            "settled_revenue_cents",
            "provider_cost_cents",
            "known_gross_profit_cents",
            "gross_profit_cents",
            "gross_margin",
        )
        for comparison_name, (baseline_start, baseline_end) in (
            comparison_windows.items()
        ):
            baseline = AdminAnalyticsService.operating_series(
                session,
                start=baseline_start,
                end=baseline_end,
                granularity=granularity,
                _include_comparisons=False,
            )
            baseline_available = baseline["data_status"] == "available"
            cost_complete = (
                totals["cost_reconciliation_status"] == "complete"
                and baseline["totals"]["cost_reconciliation_status"]
                == "complete"
            )
            comparisons[comparison_name] = {
                "status": (
                    "unavailable"
                    if not baseline_available
                    else "available"
                    if cost_complete
                    else "partial"
                ),
                "baseline_start_time": baseline["start_time"],
                "baseline_end_time": baseline["end_time"],
                "baseline_data_status": baseline["data_status"],
                "baseline_totals": (
                    baseline["totals"] if baseline_available else None
                ),
                "metrics": {
                    metric_name: _comparison_metric(
                        totals[metric_name],
                        baseline["totals"][metric_name],
                        baseline_available=baseline_available,
                    )
                    for metric_name in metric_names
                },
            }
        result["comparisons"] = comparisons
        return result

    @staticmethod
    def task_operations(
        session: Session, *, start: datetime, end: datetime
    ) -> dict[str, Any]:
        window = validate_window(start=start, end=end)
        tasks = list(
            session.scalars(
                select(GenerationTask).where(
                    GenerationTask.company_id.is_not(None),
                    GenerationTask.personal_workspace_id.is_(None),
                    GenerationTask.created_at >= window.start,
                    GenerationTask.created_at < window.end,
                )
            ).all()
        )
        status_counts = {status.value: 0 for status in TaskStatus}
        failure_reasons: Counter[str] = Counter()
        terminal_latencies: list[float] = []
        succeeded_latencies: list[float] = []
        failed_latencies: list[float] = []
        for task in tasks:
            status_counts[_enum_value(task.status)] += 1
            if task.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                latency = _duration_seconds(task.created_at, task.updated_at)
                terminal_latencies.append(latency)
                if task.status == TaskStatus.SUCCEEDED:
                    succeeded_latencies.append(latency)
                elif task.status == TaskStatus.FAILED:
                    failed_latencies.append(latency)
                    failure_reasons[_safe_error_code(task)] += 1

        task_ids = [task.id for task in tasks]
        unknown_task_ids: set[str] = set()
        if task_ids:
            unknown_task_ids = set(
                session.scalars(
                    select(RelaySubmissionOutbox.task_id).where(
                        RelaySubmissionOutbox.task_id.in_(task_ids),
                        or_(
                            RelaySubmissionOutbox.status
                            == RelayOutboxStatus.RECONCILIATION_REQUIRED,
                            RelaySubmissionOutbox.submission_outcome_uncertain_at.is_not(None),
                        ),
                    )
                ).all()
            )
        timeout_events = session.execute(
            select(TaskTimeoutEvent.task_id, TaskTimeoutEvent.created_at).where(
                    TaskTimeoutEvent.company_id.is_not(None),
                    TaskTimeoutEvent.personal_workspace_id.is_(None),
                    TaskTimeoutEvent.created_at >= window.start,
                    TaskTimeoutEvent.created_at < window.end,
                )
        ).all()
        stage_events = list(
            session.scalars(
                select(RelayTaskStageEvent).where(
                    RelayTaskStageEvent.company_id.is_not(None),
                    RelayTaskStageEvent.occurred_at >= window.start,
                    RelayTaskStageEvent.occurred_at < window.end,
                )
            ).all()
        )
        callback_events = list(
            session.scalars(
                select(RelayCallbackEvent).where(
                    RelayCallbackEvent.company_id.is_not(None),
                    RelayCallbackEvent.personal_workspace_id.is_(None),
                    RelayCallbackEvent.occurred_at >= window.start,
                    RelayCallbackEvent.occurred_at < window.end,
                )
            ).all()
        )
        stage_event_counts: Counter[str] = Counter()
        stage_task_ids: dict[str, set[str]] = defaultdict(set)
        stage_durations: dict[str, list[float]] = defaultdict(list)
        for event in stage_events:
            stage = _enum_value(event.stage)
            stage_event_counts[stage] += 1
            stage_task_ids[stage].add(event.task_id)
            if event.duration_ms is not None:
                stage_durations[stage].append(float(event.duration_ms))
        callback_delivery_latencies_ms = [
            max(
                0.0,
                (_utc(event.received_at) - _utc(event.occurred_at)).total_seconds()
                * 1000,
            )
            for event in callback_events
        ]
        terminal_callback_count = sum(
            1
            for event in callback_events
            if event.relay_status in {"succeeded", "failed", "cancelled"}
        )
        terminal_count = sum(
            status_counts[key] for key in ("succeeded", "failed", "cancelled")
        )

        trend_granularity = _task_trend_granularity(window)
        trend_points: list[dict[str, Any]] = []
        if tasks or timeout_events:
            trend_statuses: dict[date, Counter[str]] = defaultdict(Counter)
            trend_latencies: dict[date, list[float]] = defaultdict(list)
            trend_failures: dict[date, Counter[str]] = defaultdict(Counter)
            trend_timeouts: Counter[date] = Counter()
            trend_unknown: Counter[date] = Counter()
            for task in tasks:
                bucket = _bucket_start(task.created_at, trend_granularity)
                status = _enum_value(task.status)
                trend_statuses[bucket][status] += 1
                if task.id in unknown_task_ids:
                    trend_unknown[bucket] += 1
                if task.status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }:
                    trend_latencies[bucket].append(
                        _duration_seconds(task.created_at, task.updated_at)
                    )
                if task.status == TaskStatus.FAILED:
                    trend_failures[bucket][_safe_error_code(task)] += 1
            for _, occurred_at in timeout_events:
                trend_timeouts[
                    _bucket_start(occurred_at, trend_granularity)
                ] += 1
            for bucket in _bucket_sequence(window, trend_granularity):
                counts = {
                    status.value: int(trend_statuses[bucket][status.value])
                    for status in TaskStatus
                }
                terminal = sum(
                    counts[key] for key in ("succeeded", "failed", "cancelled")
                )
                trend_points.append(
                    {
                        "bucket_start": bucket.isoformat(),
                        "bucket_end": min(
                            _bucket_end(bucket, trend_granularity), window.end
                        ).isoformat(),
                        "status_counts": counts,
                        "total_task_count": sum(counts.values()),
                        "terminal_task_count": terminal,
                        "failed_count": counts["failed"],
                        "success_rate": (
                            round(counts["succeeded"] / terminal, 6)
                            if terminal
                            else None
                        ),
                        "timeout_count": int(trend_timeouts[bucket]),
                        "submission_unknown_count": int(trend_unknown[bucket]),
                        "terminal_latency_p50_seconds": _percentile(
                            trend_latencies[bucket], 0.50
                        ),
                        "terminal_latency_p95_seconds": _percentile(
                            trend_latencies[bucket], 0.95
                        ),
                        "failure_reasons": [
                            {"error_code": code, "count": count}
                            for code, count in sorted(
                                trend_failures[bucket].items(),
                                key=lambda item: (-item[1], item[0]),
                            )
                        ],
                    }
                )
        return {
            "start_time": window.start.isoformat(),
            "end_time": window.end.isoformat(),
            "status_counts": status_counts,
            "total_task_count": len(tasks),
            "terminal_task_count": terminal_count,
            "success_rate": (
                round(status_counts["succeeded"] / terminal_count, 6)
                if terminal_count
                else None
            ),
            "timeout_count": len(timeout_events),
            "submission_unknown_count": len(unknown_task_ids),
            "latency_seconds": {
                "terminal_p50": _percentile(terminal_latencies, 0.50),
                "terminal_p95": _percentile(terminal_latencies, 0.95),
                "succeeded_p50": _percentile(succeeded_latencies, 0.50),
                "succeeded_p95": _percentile(succeeded_latencies, 0.95),
                "failed_p50": _percentile(failed_latencies, 0.50),
                "failed_p95": _percentile(failed_latencies, 0.95),
            },
            "failure_reasons": [
                {"error_code": code, "count": count}
                for code, count in sorted(
                    failure_reasons.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "trend_data_status": (
                "available" if trend_points else "empty"
            ),
            "trend_granularity": trend_granularity,
            "trend_points": trend_points,
            "terminal_latency_distribution_seconds": _latency_distribution(
                terminal_latencies
            ),
            "relay_stage_source_status": (
                "available" if stage_events else "unavailable"
            ),
            "relay_stage_last_event_at": (
                max(_utc(event.occurred_at) for event in stage_events).isoformat()
                if stage_events
                else None
            ),
            "relay_stage_event_counts": {
                stage.value: (
                    int(stage_event_counts[stage.value]) if stage_events else None
                )
                for stage in RelayTaskStage
            },
            "relay_stage_task_counts": {
                stage.value: (
                    len(stage_task_ids[stage.value]) if stage_events else None
                )
                for stage in RelayTaskStage
            },
            "relay_stage_latency_ms": {
                stage.value: {
                    "sample_count": len(stage_durations[stage.value]),
                    "p50": _percentile(stage_durations[stage.value], 0.50),
                    "p95": _percentile(stage_durations[stage.value], 0.95),
                }
                for stage in RelayTaskStage
            }
            if stage_events
            else None,
            "artifact_pipeline": {
                "source_status": (
                    "available" if stage_events else "unavailable"
                ),
                "transferring_task_count": (
                    len(stage_task_ids[RelayTaskStage.ARTIFACT_TRANSFERRING.value])
                    if stage_events
                    else None
                ),
                "stored_task_count": (
                    len(stage_task_ids[RelayTaskStage.ARTIFACT_STORED.value])
                    if stage_events
                    else None
                ),
                "stored_duration_ms": (
                    {
                        "sample_count": len(
                            stage_durations[RelayTaskStage.ARTIFACT_STORED.value]
                        ),
                        "p50": _percentile(
                            stage_durations[RelayTaskStage.ARTIFACT_STORED.value],
                            0.50,
                        ),
                        "p95": _percentile(
                            stage_durations[RelayTaskStage.ARTIFACT_STORED.value],
                            0.95,
                        ),
                    }
                    if stage_events
                    else None
                ),
            },
            "relay_callbacks": {
                "source_status": (
                    "available" if callback_events else "unavailable"
                ),
                "event_count": len(callback_events) if callback_events else None,
                "terminal_event_count": (
                    terminal_callback_count if callback_events else None
                ),
                "last_event_at": (
                    max(_utc(event.occurred_at) for event in callback_events).isoformat()
                    if callback_events
                    else None
                ),
                "delivery_latency_ms": (
                    {
                        "sample_count": len(callback_delivery_latencies_ms),
                        "p50": _percentile(callback_delivery_latencies_ms, 0.50),
                        "p95": _percentile(callback_delivery_latencies_ms, 0.95),
                    }
                    if callback_events
                    else None
                ),
            },
        }

    @staticmethod
    def model_profitability(
        session: Session,
        *,
        start: datetime,
        end: datetime,
        include_inactive: bool = True,
    ) -> dict[str, Any]:
        window = validate_window(start=start, end=end)
        model_statement = select(ModelDefinition).order_by(
            ModelDefinition.display_name, ModelDefinition.id
        )
        if not include_inactive:
            model_statement = model_statement.where(ModelDefinition.active.is_(True))
        models = list(session.scalars(model_statement).all())
        model_ids = [model.id for model in models]

        tasks_by_model: dict[str, list[GenerationTask]] = defaultdict(list)
        if model_ids:
            tasks = session.scalars(
                select(GenerationTask).where(
                    GenerationTask.model_id.in_(model_ids),
                    GenerationTask.company_id.is_not(None),
                    GenerationTask.personal_workspace_id.is_(None),
                    GenerationTask.created_at >= window.start,
                    GenerationTask.created_at < window.end,
                )
            ).all()
            for task in tasks:
                tasks_by_model[task.model_id].append(task)

        settlement_rows = session.execute(
            select(
                GenerationTask.model_id,
                GenerationTask.id,
                func.coalesce(func.sum(LedgerEntry.amount_cents), 0),
            )
            .join(GenerationTask, GenerationTask.id == LedgerEntry.task_id)
            .where(
                LedgerEntry.kind == LedgerKind.SETTLE,
                LedgerEntry.created_at >= window.start,
                LedgerEntry.created_at < window.end,
                GenerationTask.model_id.in_(model_ids),
                GenerationTask.company_id.is_not(None),
                GenerationTask.personal_workspace_id.is_(None),
            )
            .group_by(GenerationTask.model_id, GenerationTask.id)
        ).all()
        revenue_by_model: Counter[str] = Counter()
        settled_task_ids_by_model: dict[str, set[str]] = defaultdict(set)
        for model_id, task_id, amount in settlement_rows:
            revenue_by_model[model_id] += int(amount)
            settled_task_ids_by_model[model_id].add(task_id)
        settled_task_ids = sorted(
            task_id
            for task_ids in settled_task_ids_by_model.values()
            for task_id in task_ids
        )
        cost_by_model: Counter[str] = Counter()
        if settled_task_ids:
            for model_id, amount in session.execute(
                select(
                    GenerationTask.model_id,
                    func.coalesce(func.sum(ChannelCostEntry.amount_cents), 0),
                )
                .join(GenerationTask, GenerationTask.id == ChannelCostEntry.task_id)
                .where(
                    ChannelCostEntry.task_id.in_(settled_task_ids),
                    ChannelCostEntry.company_id.is_not(None),
                    ChannelCostEntry.personal_workspace_id.is_(None),
                )
                .group_by(GenerationTask.model_id)
            ).all():
                cost_by_model[model_id] = int(amount)
        cost_task_ids = (
            set(
                session.scalars(
                    select(ChannelCostEntry.task_id).where(
                        ChannelCostEntry.task_id.in_(settled_task_ids),
                        ChannelCostEntry.company_id.is_not(None),
                        ChannelCostEntry.personal_workspace_id.is_(None),
                    )
                ).all()
            )
            if settled_task_ids
            else set()
        )
        unattributed_cost = int(
            session.scalar(
                select(func.coalesce(func.sum(ChannelCostEntry.amount_cents), 0)).where(
                    ChannelCostEntry.task_id.is_(None),
                    ChannelCostEntry.company_id.is_not(None),
                    ChannelCostEntry.personal_workspace_id.is_(None),
                    ChannelCostEntry.occurred_at >= window.start,
                    ChannelCostEntry.occurred_at < window.end,
                )
            )
            or 0
        )

        rows: list[dict[str, Any]] = []
        for model in models:
            model_tasks = tasks_by_model.get(model.id, [])
            counts = Counter(_enum_value(task.status) for task in model_tasks)
            terminal = sum(counts[key] for key in ("succeeded", "failed", "cancelled"))
            missing_count = sum(
                task_id not in cost_task_ids
                for task_id in settled_task_ids_by_model.get(model.id, set())
            )
            revenue = revenue_by_model.get(model.id, 0)
            cost = cost_by_model.get(model.id, 0)
            known_profit = revenue - cost
            terminal_latencies = [
                _duration_seconds(task.created_at, task.updated_at)
                for task in model_tasks
                if task.status
                in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            ]
            rows.append(
                {
                    "model_id": model.id,
                    "model_slug": model.slug,
                    "display_name": model.display_name,
                    "active": model.active,
                    "billing_mode": model.billing_mode,
                    "task_count": len(model_tasks),
                    "succeeded_count": counts["succeeded"],
                    "failed_count": counts["failed"],
                    "cancelled_count": counts["cancelled"],
                    "success_rate": (
                        round(counts["succeeded"] / terminal, 6) if terminal else None
                    ),
                    "settled_revenue_cents": revenue,
                    "provider_cost_cents": cost,
                    "known_gross_profit_cents": known_profit,
                    "gross_profit_cents": known_profit if missing_count == 0 else None,
                    "gross_margin": (
                        round(known_profit / revenue, 6)
                        if missing_count == 0 and revenue
                        else None
                    ),
                    "cost_missing_task_count": missing_count,
                    "cost_reconciliation_status": (
                        "complete" if missing_count == 0 else "incomplete"
                    ),
                    "average_terminal_latency_seconds": (
                        round(sum(terminal_latencies) / len(terminal_latencies), 3)
                        if terminal_latencies
                        else None
                    ),
                    "latency_p50_seconds": _percentile(terminal_latencies, 0.50),
                    "latency_p95_seconds": _percentile(terminal_latencies, 0.95),
                }
            )
        rows.sort(
            key=lambda row: (
                -int(row["settled_revenue_cents"]),
                str(row["display_name"]),
                str(row["model_id"]),
            )
        )
        return {
            "start_time": window.start.isoformat(),
            "end_time": window.end.isoformat(),
            "unattributed_provider_cost_cents": unattributed_cost,
            "items": rows,
        }

    @staticmethod
    def company_health(
        session: Session,
        *,
        page: int,
        page_size: int,
        now: datetime,
        low_balance_threshold_cents: int = 0,
        inactivity_days: int = 30,
        stale_reservation_hours: int = 24,
        failure_rate_threshold: float = 0.30,
        minimum_terminal_tasks: int = 5,
        abnormal_spend_ratio: float = 3.0,
    ) -> dict[str, Any]:
        if low_balance_threshold_cents < 0:
            raise ConflictError("low balance threshold cannot be negative")
        current = _utc(now)
        total = int(session.scalar(select(func.count(Company.id))) or 0)
        companies = list(
            session.scalars(
                select(Company)
                .order_by(Company.name, Company.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        company_ids = [company.id for company in companies]
        wallets = {
            company_id: (int(available), int(reserved))
            for company_id, available, reserved in session.execute(
                select(
                    WalletAccount.company_id,
                    WalletAccount.available_cents,
                    WalletAccount.reserved_cents,
                ).where(WalletAccount.company_id.in_(company_ids))
            ).all()
        }
        recent_cutoff = current - timedelta(days=30)
        inactivity_cutoff = current - timedelta(days=inactivity_days)
        task_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"total": 0, "failed": 0, "terminal": 0, "last": None}
        )
        for company_id, status, created_at in session.execute(
            select(
                GenerationTask.company_id,
                GenerationTask.status,
                GenerationTask.created_at,
            ).where(
                GenerationTask.company_id.in_(company_ids),
                GenerationTask.created_at >= recent_cutoff,
            )
        ).all():
            stats = task_stats[company_id]
            stats["total"] += 1
            stats["last"] = max(
                filter(None, (stats["last"], _utc(created_at))), default=_utc(created_at)
            )
            if status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                stats["terminal"] += 1
            if status == TaskStatus.FAILED:
                stats["failed"] += 1

        # Last task can be older than the operational 30-day window.
        for company_id, last_created in session.execute(
            select(GenerationTask.company_id, func.max(GenerationTask.created_at))
            .where(GenerationTask.company_id.in_(company_ids))
            .group_by(GenerationTask.company_id)
        ).all():
            task_stats[company_id]["last"] = _utc(last_created)

        stale_cutoff = current - timedelta(hours=stale_reservation_hours)
        stale_reservations = dict(
            session.execute(
                select(GenerationTask.company_id, func.count(GenerationTask.id))
                .where(
                    GenerationTask.company_id.in_(company_ids),
                    GenerationTask.reserved_cents > 0,
                    GenerationTask.status.in_(
                        (TaskStatus.DRAFT, TaskStatus.QUEUED, TaskStatus.PROCESSING)
                    ),
                    GenerationTask.created_at < stale_cutoff,
                )
                .group_by(GenerationTask.company_id)
            ).all()
        )

        spend_24h = defaultdict(int)
        baseline_7d = defaultdict(int)
        for company_id, amount, occurred_at in session.execute(
            select(
                LedgerEntry.company_id,
                LedgerEntry.amount_cents,
                LedgerEntry.created_at,
            ).where(
                LedgerEntry.company_id.in_(company_ids),
                LedgerEntry.kind == LedgerKind.SETTLE,
                LedgerEntry.created_at >= current - timedelta(days=8),
                LedgerEntry.created_at < current,
            )
        ).all():
            occurred = _utc(occurred_at)
            if occurred >= current - timedelta(days=1):
                spend_24h[company_id] += int(amount)
            elif occurred >= current - timedelta(days=8):
                baseline_7d[company_id] += int(amount)

        # Expiry fields are intentionally not inferred from capability overrides.
        # Once the dedicated grant metadata columns land, this code automatically
        # starts reporting them without changing the response contract.
        expiry_supported = hasattr(CompanyModelGrant, "expires_at") and hasattr(
            CompanyResourceGrant, "expires_at"
        )
        expiring_by_company: Counter[str] = Counter()
        expired_by_company: Counter[str] = Counter()
        if expiry_supported and company_ids:
            expiry_limit = current + timedelta(days=14)
            for grant_model in (CompanyModelGrant, CompanyResourceGrant):
                for company_id, expires_at in session.execute(
                    select(grant_model.company_id, grant_model.expires_at).where(  # type: ignore[attr-defined]
                        grant_model.company_id.in_(company_ids),
                        grant_model.enabled.is_(True),
                        grant_model.expires_at.is_not(None),  # type: ignore[attr-defined]
                    )
                ).all():
                    expires = _utc(expires_at)
                    if expires <= current:
                        expired_by_company[company_id] += 1
                    elif expires <= expiry_limit:
                        expiring_by_company[company_id] += 1

        rows: list[dict[str, Any]] = []
        alert_totals: Counter[str] = Counter()
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        for company in companies:
            available, reserved = wallets.get(company.id, (0, 0))
            stats = task_stats[company.id]
            alerts: list[dict[str, Any]] = []

            def add_alert(code: str, severity: str, **details: Any) -> None:
                alert_totals[code] += 1
                alerts.append({"code": code, "severity": severity, "details": details})

            if available <= low_balance_threshold_cents:
                add_alert(
                    "LOW_BALANCE",
                    "critical" if available == 0 else "warning",
                    available_cents=available,
                    threshold_cents=low_balance_threshold_cents,
                )
            stale_count = int(stale_reservations.get(company.id, 0))
            if reserved > 0 and stale_count:
                add_alert(
                    "STALE_RESERVED_BALANCE",
                    "critical",
                    reserved_cents=reserved,
                    stale_task_count=stale_count,
                    threshold_hours=stale_reservation_hours,
                )
            last_task = stats["last"]
            if _utc(company.created_at) < inactivity_cutoff and (
                last_task is None or last_task < inactivity_cutoff
            ):
                add_alert(
                    "INACTIVE_COMPANY",
                    "info",
                    last_task_at=last_task.isoformat() if last_task else None,
                    threshold_days=inactivity_days,
                )
            terminal = int(stats["terminal"])
            failed = int(stats["failed"])
            failure_rate = failed / terminal if terminal else None
            if (
                terminal >= minimum_terminal_tasks
                and failure_rate is not None
                and failure_rate >= failure_rate_threshold
            ):
                add_alert(
                    "HIGH_FAILURE_RATE",
                    "warning",
                    failure_rate=round(failure_rate, 6),
                    failed_count=failed,
                    terminal_count=terminal,
                )
            baseline_daily = baseline_7d[company.id] / 7
            if (
                baseline_daily > 0
                and spend_24h[company.id] >= baseline_daily * abnormal_spend_ratio
            ):
                add_alert(
                    "ABNORMAL_SPEND",
                    "warning",
                    spend_24h_cents=spend_24h[company.id],
                    baseline_daily_cents=round(baseline_daily),
                    ratio=round(spend_24h[company.id] / baseline_daily, 3),
                )
            if expired_by_company[company.id]:
                add_alert(
                    "ENTITLEMENT_EXPIRED",
                    "critical",
                    count=expired_by_company[company.id],
                )
            if expiring_by_company[company.id]:
                add_alert(
                    "ENTITLEMENT_EXPIRING",
                    "warning",
                    count=expiring_by_company[company.id],
                )
            alerts.sort(key=lambda item: (severity_order[item["severity"]], item["code"]))
            rows.append(
                {
                    "company_id": company.id,
                    "company_name": company.name,
                    "company_status": _enum_value(company.status),
                    "available_cents": available,
                    "reserved_cents": reserved,
                    "last_task_at": last_task.isoformat() if last_task else None,
                    "task_count_30d": int(stats["total"]),
                    "failure_rate_30d": (
                        round(failure_rate, 6) if failure_rate is not None else None
                    ),
                    "spend_24h_cents": spend_24h[company.id],
                    "alerts": alerts,
                }
            )
        rows.sort(
            key=lambda row: (
                min(
                    (severity_order[alert["severity"]] for alert in row["alerts"]),
                    default=99,
                ),
                -len(row["alerts"]),
                row["company_name"],
            )
        )
        return {
            "generated_at": current.isoformat(),
            "page": page,
            "page_size": page_size,
            "total_companies": total,
            "entitlement_expiry_data_status": (
                "available" if expiry_supported else "unavailable"
            ),
            "alert_counts": dict(sorted(alert_totals.items())),
            "items": rows,
        }

    @staticmethod
    def channel_health_summary(
        session: Session, *, start: datetime, end: datetime
    ) -> dict[str, Any]:
        window = validate_window(start=start, end=end, max_days=90)
        channel_cost: Counter[str] = Counter()
        cost_evidence_keys: set[str] = set()
        channel_type: dict[str, str] = {}
        for key, kind, amount in session.execute(
            select(
                ChannelCostEntry.channel_key,
                ChannelCostEntry.channel_type,
                ChannelCostEntry.amount_cents,
            ).where(
                ChannelCostEntry.occurred_at >= window.start,
                ChannelCostEntry.occurred_at < window.end,
            )
        ).all():
            channel_type[key] = _enum_value(kind)
            cost_evidence_keys.add(key)
            channel_cost[key] += int(amount)

        failed_by_route: Counter[str] = Counter()
        failure_codes: Counter[str] = Counter()
        route_failure_codes: dict[str, Counter[str]] = defaultdict(Counter)
        failed_tasks = session.scalars(
            select(GenerationTask).where(
                GenerationTask.status == TaskStatus.FAILED,
                GenerationTask.updated_at >= window.start,
                GenerationTask.updated_at < window.end,
            )
        ).all()
        for task in failed_tasks:
            code = _safe_error_code(task)
            failure_codes[code] += 1
            route_id = _safe_route_id(task)
            if route_id:
                failed_by_route[route_id] += 1
                route_failure_codes[route_id][code] += 1

        current = datetime.now(timezone.utc)
        latest_seen = session.scalar(
            select(RelayOperationsSnapshot)
            .where(
                RelayOperationsSnapshot.observed_at >= window.start,
                RelayOperationsSnapshot.observed_at < window.end,
            )
            .order_by(
                RelayOperationsSnapshot.observed_at.desc(),
                RelayOperationsSnapshot.id.desc(),
            )
            .limit(1)
        )
        latest = session.scalar(
            select(RelayOperationsSnapshot)
            .where(
                RelayOperationsSnapshot.observed_at >= window.start,
                RelayOperationsSnapshot.observed_at < window.end,
                RelayOperationsSnapshot.expires_at > current,
            )
            .order_by(
                RelayOperationsSnapshot.observed_at.desc(),
                RelayOperationsSnapshot.id.desc(),
            )
            .limit(1)
        )
        route_rows = (
            list(
                session.scalars(
                    select(RelayRouteOperationsSnapshot)
                    .where(
                        RelayRouteOperationsSnapshot.snapshot_id == latest.id
                    )
                    .order_by(
                        RelayRouteOperationsSnapshot.channel_key,
                        RelayRouteOperationsSnapshot.route_id,
                    )
                ).all()
            )
            if latest is not None
            else []
        )
        routes_by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for route in route_rows:
            successful = int(route.successful_task_count)
            failed = int(route.failed_task_count)
            terminal = successful + failed
            routes_by_channel[route.channel_key].append(
                {
                    "route_id": int(route.route_id),
                    "channel_key": route.channel_key,
                    "channel_type": _enum_value(route.channel_type),
                    "provider_name": route.provider_name,
                    "model": route.model,
                    "mode": route.mode,
                    "enabled": bool(route.enabled),
                    "production_ready": bool(route.production_ready),
                    "health_status": route.health_status,
                    "failure_code": route.failure_code or None,
                    "last_probe_at": (
                        _utc(route.last_probe_at).isoformat()
                        if route.last_probe_at is not None
                        else None
                    ),
                    "rpm_limit": int(route.rpm_limit),
                    "rpm_used": int(route.rpm_used),
                    "active_task_count": int(route.active_task_count),
                    "task_capacity": int(route.task_capacity),
                    "cooling_account_count": int(route.cooling_account_count),
                    "invalid_account_count": int(route.invalid_account_count),
                    "busy_account_count": int(route.busy_account_count),
                    "rate_limited_account_count": int(
                        route.rate_limited_account_count
                    ),
                    "successful_task_count": successful,
                    "failed_task_count": failed,
                    "observed_success_rate": (
                        round(successful / terminal, 6) if terminal else None
                    ),
                    "latency_p50_ms": (
                        int(route.latency_p50_ms)
                        if route.latency_p50_ms is not None
                        else None
                    ),
                    "latency_p95_ms": (
                        int(route.latency_p95_ms)
                        if route.latency_p95_ms is not None
                        else None
                    ),
                }
            )

        all_channel_keys = sorted(set(routes_by_channel) | set(channel_cost))
        rows: list[dict[str, Any]] = []
        for key in all_channel_keys:
            routes = routes_by_channel[key]
            if not routes:
                rows.append(
                    {
                        "channel_key": key,
                        "channel_type": channel_type.get(key),
                        "source_status": "unavailable",
                        "route_count": None,
                        "successful_task_count": None,
                        "failed_task_count": None,
                        "observed_success_rate": None,
                        "latency_p50_ms": None,
                        "latency_p95_ms": None,
                        "latency_data_status": "unavailable",
                        "provider_cost_cents": int(channel_cost[key]),
                        "provider_cost_data_status": "available",
                        "routes": [],
                    }
                )
                continue
            successful = sum(route["successful_task_count"] for route in routes)
            failed = sum(route["failed_task_count"] for route in routes)
            terminal = successful + failed
            single_route = routes[0] if len(routes) == 1 else None
            rows.append(
                {
                    "channel_key": key,
                    "channel_type": routes[0]["channel_type"],
                    "source_status": "available",
                    "route_count": len(routes),
                    "successful_task_count": successful,
                    "failed_task_count": failed,
                    "observed_success_rate": (
                        round(successful / terminal, 6) if terminal else None
                    ),
                    # Percentiles cannot be combined from per-route percentiles
                    # without the original samples. Preserve exactness by
                    # exposing them only for a single route and always retaining
                    # the signed route detail below.
                    "latency_p50_ms": (
                        single_route["latency_p50_ms"] if single_route else None
                    ),
                    "latency_p95_ms": (
                        single_route["latency_p95_ms"] if single_route else None
                    ),
                    "latency_data_status": (
                        "available" if single_route else "route_detail_only"
                    ),
                    "provider_cost_cents": (
                        int(channel_cost[key]) if key in cost_evidence_keys else None
                    ),
                    "provider_cost_data_status": (
                        "available" if key in cost_evidence_keys else "unavailable"
                    ),
                    "routes": routes,
                }
            )

        operational_codes = {
            "PROVIDER_ACCOUNT_POOL_RATE_LIMITED": "rate_limited",
            "PROVIDER_ACCOUNT_POOL_BUSY": "pool_busy",
            "PROVIDER_CIRCUIT_OPEN": "circuit_open",
            "SUBMISSION_RECONCILIATION_REQUIRED": "submission_unknown",
            "PROVIDER_POLL_RECONCILIATION_REQUIRED": "poll_unknown",
        }
        telemetry_available = latest is not None
        freshness = (
            "fresh"
            if latest is not None and latest.monitor_fresh
            else "degraded"
            if latest is not None
            else "expired"
            if latest_seen is not None
            else "unavailable"
        )
        return {
            "start_time": window.start.isoformat(),
            "end_time": window.end.isoformat(),
            "evidence_source": (
                "signed_relay_operations_snapshot_and_platform_cost_ledger"
                if telemetry_available
                else "platform_cost_ledger_only"
            ),
            "source_status": "available" if telemetry_available else "unavailable",
            "freshness": freshness,
            "last_snapshot_at": (
                _utc(latest_seen.observed_at).isoformat()
                if latest_seen is not None
                else None
            ),
            "snapshot_expires_at": (
                _utc(latest.expires_at).isoformat() if latest is not None else None
            ),
            "relay_control_plane_data_status": (
                "available" if telemetry_available else "unavailable"
            ),
            "monitor": (
                {
                    "fresh": bool(latest.monitor_fresh),
                    "last_completed_at": (
                        _utc(latest.monitor_last_completed_at).isoformat()
                        if latest.monitor_last_completed_at is not None
                        else None
                    ),
                }
                if latest is not None
                else None
            ),
            "operations_window": (
                {
                    "start_time": _utc(latest.window_started_at).isoformat(),
                    "end_time": _utc(latest.observed_at).isoformat(),
                }
                if latest is not None
                else None
            ),
            "account_pool_metrics": (
                {
                    "total_account_count": int(latest.account_total),
                    "active_account_count": int(latest.account_active),
                    "cooling_account_count": int(latest.account_cooling),
                    "invalid_account_count": int(latest.account_invalid),
                    "busy_account_count": int(latest.account_busy),
                    "rate_limited_account_count": int(
                        latest.account_rate_limited
                    ),
                    "active_task_count": int(latest.account_active_tasks),
                    "task_capacity": int(latest.account_task_capacity),
                    "rpm_limit": sum(int(route.rpm_limit) for route in route_rows),
                    "rpm_used": sum(int(route.rpm_used) for route in route_rows),
                    "rate_limit_count": int(latest.task_rate_limited_count),
                    "failover_count": int(latest.task_failover_count),
                    "pending_alert_count": int(
                        latest.delivery_pending_alert_count
                    ),
                    "dead_letter_alert_count": int(
                        latest.delivery_dead_alert_count
                    ),
                }
                if latest is not None
                else {
                    "total_account_count": None,
                    "active_account_count": None,
                    "cooling_account_count": None,
                    "invalid_account_count": None,
                    "busy_account_count": None,
                    "rate_limited_account_count": None,
                    "active_task_count": None,
                    "task_capacity": None,
                    "rpm_limit": None,
                    "rpm_used": None,
                    "rate_limit_count": None,
                    "failover_count": None,
                    "pending_alert_count": None,
                    "dead_letter_alert_count": None,
                }
            ),
            "relay_task_metrics": (
                {
                    "queued": int(latest.task_queued),
                    "submitting": int(latest.task_submitting),
                    "submission_unknown": int(latest.task_submission_unknown),
                    "provider_processing": int(latest.task_provider_processing),
                    "artifact_transferring": int(
                        latest.task_artifact_transferring
                    ),
                    "succeeded": int(latest.task_succeeded),
                    "failed": int(latest.task_failed),
                    "cancelled": int(latest.task_cancelled),
                }
                if latest is not None
                else None
            ),
            "delivery_backlogs": (
                {
                    "pending_alert_count": int(
                        latest.delivery_pending_alert_count
                    ),
                    "dead_letter_alert_count": int(
                        latest.delivery_dead_alert_count
                    ),
                    "oldest_pending_alert_at": (
                        _utc(latest.delivery_oldest_pending_alert_at).isoformat()
                        if latest.delivery_oldest_pending_alert_at is not None
                        else None
                    ),
                    "pending_cost_count": int(latest.delivery_pending_cost_count),
                    "dead_letter_cost_count": int(
                        latest.delivery_dead_cost_count
                    ),
                    "pending_task_stage_count": int(
                        latest.delivery_pending_task_stage_count
                    ),
                    "dead_letter_task_stage_count": int(
                        latest.delivery_dead_task_stage_count
                    ),
                    "pending_snapshot_count": int(
                        latest.delivery_pending_snapshot_count
                    ),
                    "dead_letter_snapshot_count": int(
                        latest.delivery_dead_snapshot_count
                    ),
                }
                if latest is not None
                else None
            ),
            "cost_delivery_summary": (
                {
                    "successful_relay_jobs": int(latest.cost_successful_jobs),
                    "explicit_cost_relay_jobs": int(latest.cost_explicit_jobs),
                    "delivered_cost_relay_jobs": int(latest.cost_delivered_jobs),
                    "incomplete_relay_jobs": int(latest.cost_incomplete_jobs),
                    "native_billing_reconciliation_jobs": int(
                        latest.cost_native_reconciliation_jobs
                    ),
                    "reconciliation_complete": bool(
                        latest.cost_reconciliation_complete
                    ),
                }
                if latest is not None
                else None
            ),
            "observed_failure_signals": {
                label: int(failure_codes[code])
                for code, label in operational_codes.items()
            },
            "unattributed_failed_task_count": sum(failure_codes.values())
            - sum(failed_by_route.values()),
            "platform_failure_routes": [
                {
                    "route_id": route_id,
                    "failed_task_count": int(count),
                    "failure_codes": [
                        {"error_code": code, "count": code_count}
                        for code, code_count in sorted(
                            route_failure_codes[route_id].items(),
                            key=lambda item: (-item[1], item[0]),
                        )
                    ],
                }
                for route_id, count in sorted(failed_by_route.items())
            ],
            "channels": rows,
        }

    @staticmethod
    def data_readiness(session: Session, *, now: datetime) -> dict[str, Any]:
        current = _utc(now)

        latest_snapshot = session.scalar(
            select(RelayOperationsSnapshot)
            .order_by(
                RelayOperationsSnapshot.observed_at.desc(),
                RelayOperationsSnapshot.id.desc(),
            )
            .limit(1)
        )
        telemetry_available = (
            latest_snapshot is not None
            and _utc(latest_snapshot.expires_at) > current
        )

        cost_count, last_cost_at = session.execute(
            select(
                func.count(ChannelCostEntry.id),
                func.max(ChannelCostEntry.occurred_at),
            )
        ).one()
        missing_cost_count = session.scalar(
            select(func.count(GenerationTask.id)).where(
                GenerationTask.status == TaskStatus.SUCCEEDED,
                ~select(ChannelCostEntry.id)
                .where(ChannelCostEntry.task_id == GenerationTask.id)
                .exists(),
            )
        )

        stage_count, last_stage_at = session.execute(
            select(
                func.count(RelayTaskStageEvent.id),
                func.max(RelayTaskStageEvent.occurred_at),
            )
        ).one()
        callback_count, last_callback_at = session.execute(
            select(
                func.count(RelayCallbackEvent.id),
                func.max(RelayCallbackEvent.occurred_at),
            )
        ).one()
        publication_count, last_publication_at = session.execute(
            select(
                func.count(PublicationJob.id),
                func.max(PublicationJob.updated_at),
            )
        ).one()
        artifact_count, last_artifact_at = session.execute(
            select(
                func.count(TaskArtifact.id),
                func.max(TaskArtifact.created_at),
            )
        ).one()
        download_count, last_download_at = session.execute(
            select(
                func.count(DownloadCompletion.id),
                func.max(DownloadCompletion.completed_at),
            ).where(DownloadCompletion.verification_version == 1)
        ).one()

        def timestamp(value: datetime | None) -> str | None:
            return _utc(value).isoformat() if value is not None else None

        sources = {
            "platform_db": {
                "source_status": "available",
                "freshness": "live",
                "last_event_at": None,
                "gaps": [],
            },
            "relay_telemetry": {
                "source_status": (
                    "available" if telemetry_available else "unavailable"
                ),
                "freshness": (
                    "fresh"
                    if telemetry_available and latest_snapshot.monitor_fresh
                    else "degraded"
                    if telemetry_available
                    else "expired"
                    if latest_snapshot is not None
                    else "unavailable"
                ),
                "last_event_at": (
                    timestamp(latest_snapshot.observed_at)
                    if latest_snapshot is not None
                    else None
                ),
                "expires_at": (
                    timestamp(latest_snapshot.expires_at)
                    if latest_snapshot is not None
                    else None
                ),
                "gaps": (
                    []
                    if telemetry_available and latest_snapshot.monitor_fresh
                    else ["relay_monitor_not_fresh"]
                    if telemetry_available
                    else ["relay_operations_snapshot_expired"]
                    if latest_snapshot is not None
                    else ["no_signed_relay_operations_snapshot"]
                ),
            },
            "channel_costs": {
                "source_status": (
                    "available"
                    if int(cost_count or 0) > 0
                    and int(missing_cost_count or 0) == 0
                    else "incomplete"
                    if int(cost_count or 0) > 0
                    or int(missing_cost_count or 0) > 0
                    else "unavailable"
                ),
                "freshness": "event_driven" if cost_count else "unavailable",
                "last_event_at": timestamp(last_cost_at),
                "event_count": int(cost_count or 0),
                "missing_successful_task_count": int(missing_cost_count or 0),
                "gaps": (
                    ["successful_tasks_missing_provider_cost"]
                    if int(missing_cost_count or 0) > 0
                    else []
                    if int(cost_count or 0) > 0
                    else ["no_signed_channel_cost_events"]
                ),
            },
            "task_stages": {
                "source_status": "available" if stage_count else "unavailable",
                "freshness": "event_driven" if stage_count else "unavailable",
                "last_event_at": timestamp(last_stage_at),
                "event_count": int(stage_count or 0),
                "gaps": [] if stage_count else ["no_signed_task_stage_events"],
            },
            "relay_callbacks": {
                "source_status": "available" if callback_count else "unavailable",
                "freshness": "event_driven" if callback_count else "unavailable",
                "last_event_at": timestamp(last_callback_at),
                "event_count": int(callback_count or 0),
                "gaps": [] if callback_count else ["no_signed_relay_callbacks"],
            },
            "publishing": {
                # This source is the authoritative Platform database. A
                # successful zero-row query means "available + empty", not an
                # unavailable publishing subsystem.
                "source_status": "available",
                "data_status": "available" if publication_count else "empty",
                "freshness": "event_driven",
                "last_event_at": timestamp(last_publication_at),
                "job_count": int(publication_count or 0),
                "gaps": [] if publication_count else ["no_publication_jobs_observed"],
            },
            "artifact_and_download_evidence": {
                "source_status": "available",
                "data_status": "available" if artifact_count else "empty",
                "freshness": "event_driven",
                "last_artifact_at": timestamp(last_artifact_at),
                "last_verified_download_at": timestamp(last_download_at),
                "artifact_count": int(artifact_count or 0),
                "verified_download_count": int(download_count or 0),
                "gaps": (
                    [] if artifact_count else ["no_durable_artifacts_observed"]
                ),
            },
        }
        blocking_sources = {
            key: source["source_status"]
            for key, source in sources.items()
            if key in {"relay_telemetry", "channel_costs", "task_stages"}
            and source["source_status"] != "available"
        }
        return {
            "generated_at": current.isoformat(),
            "production_data_ready": not blocking_sources,
            "blocking_sources": blocking_sources,
            "sources": sources,
        }

    @staticmethod
    def exception_center(
        session: Session, *, now: datetime, limit_per_category: int = 50
    ) -> dict[str, Any]:
        current = _utc(now)
        company_names = {
            company_id: name for company_id, name in session.execute(select(Company.id, Company.name))
        }
        items: list[dict[str, Any]] = []
        summary: Counter[str] = Counter()

        def append_item(
            *,
            category: str,
            severity: str,
            company_id: str,
            target_type: str,
            target_id: str,
            status: str,
            occurred_at: datetime,
            error_code: str | None = None,
            actions: list[dict[str, Any]] | None = None,
        ) -> None:
            items.append(
                {
                    "category": category,
                    "severity": severity,
                    "company_id": company_id,
                    "company_name": company_names.get(company_id),
                    "target_type": target_type,
                    "target_id": target_id,
                    "status": status,
                    "error_code": error_code,
                    "occurred_at": _utc(occurred_at).isoformat(),
                    "actions": actions or [],
                }
            )

        publication_categories = {
                "pending_approval": "PUBLICATION_PENDING_APPROVAL",
                "failed": "PUBLICATION_FAILED",
                "submission_unknown": "PUBLICATION_SUBMISSION_UNKNOWN",
                "requires_reauth": "PUBLISHER_REAUTH_REQUIRED",
        }
        for status_enum in (
            PublicationJobStatus.PENDING_APPROVAL,
            PublicationJobStatus.FAILED,
            PublicationJobStatus.SUBMISSION_UNKNOWN,
            PublicationJobStatus.REQUIRES_REAUTH,
        ):
            status = _enum_value(status_enum)
            category = publication_categories[status]
            summary[category] += int(
                session.scalar(
                    select(func.count(PublicationJob.id)).where(
                        PublicationJob.status == status_enum
                    )
                )
                or 0
            )
            publication_jobs = session.scalars(
                select(PublicationJob)
                .where(PublicationJob.status == status_enum)
                .order_by(PublicationJob.updated_at.desc(), PublicationJob.id)
                .limit(limit_per_category)
            ).all()
            for job in publication_jobs:
                actions: list[dict[str, Any]] = []
                if status_enum == PublicationJobStatus.SUBMISSION_UNKNOWN:
                    actions.append(
                        {
                            "code": "reconcile_publication_submission",
                            "method": "POST",
                            "path": (
                                "/api/v1/platform-admin/analytics/exceptions/"
                                f"companies/{job.company_id}/publication-jobs/"
                                f"{job.id}/reconcile"
                            ),
                            "requires_external_verification": True,
                        }
                    )
                append_item(
                    category=category,
                    severity=(
                        "critical" if status == "submission_unknown" else "warning"
                    ),
                    company_id=job.company_id,
                    target_type="publication_job",
                    target_id=job.id,
                    status=status,
                    occurred_at=job.updated_at,
                    error_code=job.error_code,
                    actions=actions,
                )

        summary["PUBLISHER_REAUTH_REQUIRED"] += int(
            session.scalar(
                select(func.count(PublisherConnection.id)).where(
                    PublisherConnection.status
                    == PublisherConnectionStatus.REQUIRES_REAUTH
                )
            )
            or 0
        )
        connections = session.scalars(
            select(PublisherConnection)
            .where(PublisherConnection.status == PublisherConnectionStatus.REQUIRES_REAUTH)
            .order_by(PublisherConnection.updated_at.desc(), PublisherConnection.id)
            .limit(limit_per_category)
        ).all()
        for connection in connections:
            append_item(
                category="PUBLISHER_REAUTH_REQUIRED",
                severity="warning",
                company_id=connection.company_id,
                target_type="publisher_connection",
                target_id=connection.id,
                status=_enum_value(connection.status),
                occurred_at=connection.updated_at,
            )

        # OAuth expiry is metadata, never returned with the token-bearing config.
        active_connections = session.scalars(
            select(PublisherConnection)
            .where(PublisherConnection.status == PublisherConnectionStatus.ACTIVE)
            .order_by(PublisherConnection.updated_at.desc(), PublisherConnection.id)
        ).all()
        expiry_limit = current + timedelta(days=14)
        for connection in active_connections:
            raw_expiry = (connection.config or {}).get("token_expires_at")
            if not isinstance(raw_expiry, str):
                continue
            try:
                expires = _utc(datetime.fromisoformat(raw_expiry.replace("Z", "+00:00")))
            except ValueError:
                continue
            if expires <= expiry_limit:
                summary["PUBLISHER_OAUTH_EXPIRING"] += 1
                append_item(
                    category="PUBLISHER_OAUTH_EXPIRING",
                    severity="critical" if expires <= current else "warning",
                    company_id=connection.company_id,
                    target_type="publisher_connection",
                    target_id=connection.id,
                    status="expired" if expires <= current else "expiring",
                    occurred_at=connection.updated_at,
                )

        for status_enum, category in (
            (RelayOutboxStatus.RECONCILIATION_REQUIRED, "RELAY_SUBMISSION_UNKNOWN"),
            (RelayOutboxStatus.PERMANENTLY_FAILED, "RELAY_SUBMISSION_FAILED"),
        ):
            summary[category] += int(
                session.scalar(
                    select(func.count(RelaySubmissionOutbox.id)).where(
                        RelaySubmissionOutbox.status == status_enum
                    )
                )
                or 0
            )
            outboxes = session.scalars(
                select(RelaySubmissionOutbox)
                .where(RelaySubmissionOutbox.status == status_enum)
                .order_by(
                    RelaySubmissionOutbox.updated_at.desc(),
                    RelaySubmissionOutbox.id,
                )
                .limit(limit_per_category)
            ).all()
            for outbox in outboxes:
                append_item(
                    category=category,
                    severity="critical",
                    company_id=outbox.company_id,
                    target_type="generation_task",
                    target_id=outbox.task_id,
                    status=_enum_value(outbox.status),
                    occurred_at=outbox.updated_at,
                )

        transfer_codes = {"ARTIFACT_TRANSFER_FAILED", "ARTIFACT_TRANSFER_RETRYING"}
        transfer_tasks = session.scalars(
            select(GenerationTask)
            .where(GenerationTask.relay_error_snapshot.is_not(None))
            .order_by(GenerationTask.updated_at.desc(), GenerationTask.id)
        ).all()
        for task in transfer_tasks:
            code = _safe_error_code(task)
            if code not in transfer_codes:
                continue
            summary["ARTIFACT_STORAGE_TRANSFER_FAILED"] += 1
            append_item(
                category="ARTIFACT_STORAGE_TRANSFER_FAILED",
                severity="critical" if code.endswith("FAILED") else "warning",
                company_id=task.company_id,
                target_type="generation_task",
                target_id=task.id,
                status=_enum_value(task.status),
                occurred_at=task.updated_at,
                error_code=code,
            )

        for status_enum in (
            DownloadGatewayRegistrationStatus.UNKNOWN,
            DownloadGatewayRegistrationStatus.DEAD,
            DownloadGatewayRegistrationStatus.RETRY,
        ):
            status = _enum_value(status_enum)
            category = (
                "DOWNLOAD_REGISTRATION_UNKNOWN"
                if status == "unknown"
                else "DOWNLOAD_REGISTRATION_FAILED"
            )
            summary[category] += int(
                session.scalar(
                    select(func.count(DownloadGatewayRegistrationAttempt.id)).where(
                        DownloadGatewayRegistrationAttempt.status == status_enum
                    )
                )
                or 0
            )
            gateway_attempts = session.scalars(
                select(DownloadGatewayRegistrationAttempt)
                .where(DownloadGatewayRegistrationAttempt.status == status_enum)
                .order_by(
                    DownloadGatewayRegistrationAttempt.updated_at.desc(),
                    DownloadGatewayRegistrationAttempt.id,
                )
                .limit(limit_per_category)
            ).all()
            for attempt in gateway_attempts:
                actions: list[dict[str, Any]] = []
                if status_enum in {
                    DownloadGatewayRegistrationStatus.UNKNOWN,
                    DownloadGatewayRegistrationStatus.RETRY,
                }:
                    actions.append(
                        {
                            "code": "reconcile_download_registration",
                            "method": "POST",
                            "path": (
                                "/api/v1/platform-admin/"
                                "download-gateway-registration-attempts/"
                                f"{attempt.id}/reconcile"
                            ),
                            "requires_external_verification": (
                                status_enum
                                == DownloadGatewayRegistrationStatus.UNKNOWN
                            ),
                        }
                    )
                append_item(
                    category=category,
                    severity=(
                        "critical" if status in {"unknown", "dead"} else "warning"
                    ),
                    company_id=attempt.company_id,
                    target_type="download_gateway_registration_attempt",
                    target_id=attempt.id,
                    status=status,
                    occurred_at=attempt.updated_at,
                    error_code=attempt.last_error_code,
                    actions=actions,
                )

        severity_rank = {"critical": 0, "warning": 1, "info": 2}
        items.sort(
            key=lambda item: (
                severity_rank[item["severity"]],
                -datetime.fromisoformat(item["occurred_at"]).timestamp(),
                item["category"],
                item["target_id"],
            )
        )
        selected_items: list[dict[str, Any]] = []
        selected_counts: Counter[str] = Counter()
        for item in items:
            category = item["category"]
            if selected_counts[category] >= limit_per_category:
                continue
            selected_counts[category] += 1
            selected_items.append(item)

        source_categories = {
            "publishing": {
                "PUBLICATION_PENDING_APPROVAL",
                "PUBLICATION_FAILED",
                "PUBLICATION_SUBMISSION_UNKNOWN",
                "PUBLISHER_REAUTH_REQUIRED",
                "PUBLISHER_OAUTH_EXPIRING",
            },
            "relay": {
                "RELAY_SUBMISSION_UNKNOWN",
                "RELAY_SUBMISSION_FAILED",
            },
            "artifact_and_download": {
                "ARTIFACT_STORAGE_TRANSFER_FAILED",
                "DOWNLOAD_REGISTRATION_UNKNOWN",
                "DOWNLOAD_REGISTRATION_FAILED",
            },
        }
        sources: dict[str, dict[str, Any]] = {}
        for source, categories in source_categories.items():
            total = sum(int(summary[category]) for category in categories)
            returned = sum(
                1 for item in selected_items if item["category"] in categories
            )
            latest = max(
                (
                    item["occurred_at"]
                    for item in selected_items
                    if item["category"] in categories
                ),
                default=None,
            )
            sources[source] = {
                # Reaching this response proves the complete query block
                # succeeded under the endpoint's conjunctive source
                # permissions. Query failures are not swallowed into an HTTP
                # 200; FastAPI returns an error and the client records failed.
                "source_status": "available",
                "data_status": "available" if total else "empty",
                "exception_count": total,
                "returned_count": returned,
                "last_exception_at": latest,
            }
        return {
            "generated_at": current.isoformat(),
            "source_status": "available",
            "sources": sources,
            "summary": dict(sorted(summary.items())),
            "items": selected_items,
        }
