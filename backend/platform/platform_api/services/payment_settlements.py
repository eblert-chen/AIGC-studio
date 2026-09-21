from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    PaymentSettlementBatch,
    PaymentSettlementEntry,
    PaymentSettlementSourceKind,
    ProviderCostStatementBatch,
    ProviderCostStatementLine,
)
from ..payment_providers import (
    PAYMENT_PROVIDER_KEY_PATTERN,
    PAYMENT_PROVIDER_RESOURCE_ID_PATTERN,
)
from .errors import ConflictError


_PROVIDER = re.compile(rf"^{PAYMENT_PROVIDER_KEY_PATTERN}$")
_RESOURCE = re.compile(rf"^{PAYMENT_PROVIDER_RESOURCE_ID_PATTERN}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTIONAL_TYPES = {"capture", "refund", "chargeback", "dispute_reversal"}
_PSP_LINE_TYPES = _TRANSACTIONAL_TYPES | {"fee", "payout"}
_LINE_TYPES = _PSP_LINE_TYPES | {"bank_deposit"}
_PAYMENT_DOCUMENT_PARSER = "payment-settlement-json-v1"
_PROVIDER_COST_DOCUMENT_PARSER = "provider-cost-json-v1"
_UPLOAD_VERIFICATION_METHOD = "uploaded_file_digest"
_MAX_SOURCE_DOCUMENT_BYTES = 50 * 1024 * 1024
_ImportResult = TypeVar("_ImportResult")


def _sealed_import(method: Callable[..., _ImportResult]) -> Callable[..., _ImportResult]:
    """Rollback a losing insert, then compare the winner's entire sealed intent."""

    @wraps(method)
    def persist(cls: Any, session: Session, **kwargs: Any) -> _ImportResult:
        try:
            with session.begin_nested():
                return method(cls, session, **kwargs)
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
                exc.orig, "pgcode", None
            )
            sqlite_code = getattr(exc.orig, "sqlite_errorcode", None)
            if sqlstate != "23505" and sqlite_code not in {1555, 2067}:
                raise
            # READ COMMITTED sees the committed winner after the failed INSERT.
            # A savepoint keeps unrelated caller work and the outer transaction
            # usable. The normal replay path compares all bytes and metadata.
            try:
                with session.begin_nested():
                    return method(cls, session, **kwargs)
            except IntegrityError as retry_exc:
                raise ConflictError("结算导入唯一键对应不同的不可变事实") from retry_exc

    return persist


def _utc(value: datetime, *, field: str = "支付结算时间") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConflictError(f"{field}必须包含时区")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _required_text(value: str, *, field: str, max_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > max_length
    ):
        raise ConflictError(f"{field}无效")
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load_source_document(source_document_bytes: bytes, *, label: str) -> dict[str, Any]:
    if (
        not isinstance(source_document_bytes, bytes)
        or not source_document_bytes
        or len(source_document_bytes) > _MAX_SOURCE_DOCUMENT_BYTES
    ):
        raise ConflictError(f"{label}原始文件字节无效")
    try:
        value = json.loads(
            source_document_bytes.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ConflictError(f"{label}原始文件不是严格 UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ConflictError(f"{label}原始文件顶层必须是对象")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ConflictError(f"{label}字段不完整或包含未知字段")


def _document_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ConflictError(f"{field}必须是带时区的 ISO 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConflictError(f"{field}必须是带时区的 ISO 时间") from exc
    return _utc(parsed, field=field)


def _verify_source_bytes(
    source_document_bytes: bytes,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
    label: str,
) -> tuple[str, int]:
    if (
        not isinstance(source_document_bytes, bytes)
        or not source_document_bytes
        or len(source_document_bytes) > _MAX_SOURCE_DOCUMENT_BYTES
    ):
        raise ConflictError(f"{label}原始文件字节无效")
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise ConflictError(f"{label}源文件摘要无效")
    if (
        isinstance(expected_size_bytes, bool)
        or not isinstance(expected_size_bytes, int)
        or expected_size_bytes <= 0
    ):
        raise ConflictError(f"{label}源文件大小无效")
    if len(source_document_bytes) != expected_size_bytes:
        raise ConflictError(f"{label}源文件大小与实际上传字节不一致")
    actual_sha256 = hashlib.sha256(source_document_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ConflictError(f"{label}源文件摘要与实际上传字节不一致")
    return actual_sha256, len(source_document_bytes)


@dataclass(frozen=True, slots=True)
class PaymentSettlementLine:
    provider_line_id: str
    provider_transaction_id: str | None
    related_provider_reference: str | None
    line_type: str
    gross_amount_cents: int
    fee_amount_cents: int
    net_amount_cents: int
    currency: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentSettlementBatchManifest:
    batch_id: str
    provider: str
    merchant_account: str
    source_kind: str
    period_start: datetime
    period_end: datetime
    source_document_sha256: str
    lines_sha256: str
    line_count: int
    line_type_counts: dict[str, int]
    gross_amount_cents: int
    fee_amount_cents: int
    net_amount_cents: int

    def evidence_payload(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "provider": self.provider,
            "merchant_account": self.merchant_account,
            "source_kind": self.source_kind,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "source_document_sha256": self.source_document_sha256,
            "lines_sha256": self.lines_sha256,
            "line_count": self.line_count,
            "line_type_counts": dict(sorted(self.line_type_counts.items())),
            "gross_amount_cents": self.gross_amount_cents,
            "fee_amount_cents": self.fee_amount_cents,
            "net_amount_cents": self.net_amount_cents,
        }


@dataclass(frozen=True, slots=True)
class PaymentSettlementImportResult:
    batch: PaymentSettlementBatch
    entries: tuple[PaymentSettlementEntry, ...]
    created_count: int
    created_batch: bool
    manifest: PaymentSettlementBatchManifest


class PaymentSettlementImportService:
    """Archive an immutable upload; its digest is not provider authentication."""

    @classmethod
    def import_document(
        cls,
        session: Session,
        *,
        provider: str,
        merchant_account: str,
        source_kind: PaymentSettlementSourceKind | str,
        period_start: datetime,
        period_end: datetime,
        provider_document_id: str | None,
        source_document_sha256: str,
        source_document_bytes: bytes,
        source_object_key: str,
        source_object_version: str,
        source_size_bytes: int,
        parser_version: str,
    ) -> PaymentSettlementImportResult:
        actual_sha256, actual_size = _verify_source_bytes(
            source_document_bytes,
            expected_sha256=source_document_sha256,
            expected_size_bytes=source_size_bytes,
            label="支付结算",
        )
        if parser_version != _PAYMENT_DOCUMENT_PARSER:
            raise ConflictError("支付结算解析器版本不受支持")
        document = _load_source_document(source_document_bytes, label="支付结算")
        _exact_keys(
            document,
            {
                "schema_version",
                "provider",
                "merchant_account",
                "source_kind",
                "period_start",
                "period_end",
                "lines",
            },
            label="支付结算原始文件",
        )
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ConflictError("支付结算原始文件 schema_version 不受支持")
        normalized_start = _utc(period_start, field="支付结算批次开始时间")
        normalized_end = _utc(period_end, field="支付结算批次结束时间")
        document_start = _document_datetime(
            document["period_start"], field="支付结算文件 period_start"
        )
        document_end = _document_datetime(
            document["period_end"], field="支付结算文件 period_end"
        )
        normalized_source_kind = (
            source_kind.value
            if isinstance(source_kind, PaymentSettlementSourceKind)
            else source_kind
        )
        if (
            document["provider"] != provider
            or document["merchant_account"] != merchant_account
            or document["source_kind"] != normalized_source_kind
            or document_start != normalized_start
            or document_end != normalized_end
        ):
            raise ConflictError("支付结算文件头与导入作用域不一致")
        raw_lines = document["lines"]
        if not isinstance(raw_lines, list):
            raise ConflictError("支付结算文件 lines 必须是数组")
        lines: list[PaymentSettlementLine] = []
        expected_line_keys = {
            "provider_line_id",
            "provider_transaction_id",
            "related_provider_reference",
            "line_type",
            "gross_amount_cents",
            "fee_amount_cents",
            "net_amount_cents",
            "currency",
            "occurred_at",
        }
        for index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, dict):
                raise ConflictError(f"支付结算文件第 {index + 1} 行必须是对象")
            _exact_keys(
                raw_line,
                expected_line_keys,
                label=f"支付结算文件第 {index + 1} 行",
            )
            lines.append(
                PaymentSettlementLine(
                    provider_line_id=raw_line["provider_line_id"],
                    provider_transaction_id=raw_line["provider_transaction_id"],
                    related_provider_reference=raw_line[
                        "related_provider_reference"
                    ],
                    line_type=raw_line["line_type"],
                    gross_amount_cents=raw_line["gross_amount_cents"],
                    fee_amount_cents=raw_line["fee_amount_cents"],
                    net_amount_cents=raw_line["net_amount_cents"],
                    currency=raw_line["currency"],
                    occurred_at=_document_datetime(
                        raw_line["occurred_at"],
                        field=f"支付结算文件第 {index + 1} 行 occurred_at",
                    ),
                )
            )
        return cls._persist_lines(
            session,
            provider=provider,
            merchant_account=merchant_account,
            source_kind=source_kind,
            period_start=normalized_start,
            period_end=normalized_end,
            provider_document_id=provider_document_id,
            source_document_sha256=actual_sha256,
            source_document_bytes=source_document_bytes,
            source_object_key=source_object_key,
            source_object_version=source_object_version,
            source_size_bytes=actual_size,
            verification_method=_UPLOAD_VERIFICATION_METHOD,
            verified_at=datetime.now(timezone.utc),
            parser_version=parser_version,
            lines=lines,
        )

    @classmethod
    @_sealed_import
    def _persist_lines(
        cls,
        session: Session,
        *,
        provider: str,
        merchant_account: str,
        source_kind: PaymentSettlementSourceKind | str,
        period_start: datetime,
        period_end: datetime,
        provider_document_id: str | None,
        source_document_sha256: str,
        source_document_bytes: bytes,
        source_object_key: str,
        source_object_version: str,
        source_size_bytes: int,
        verification_method: str,
        verified_at: datetime,
        parser_version: str,
        lines: list[PaymentSettlementLine],
    ) -> PaymentSettlementImportResult:
        if _PROVIDER.fullmatch(provider) is None:
            raise ConflictError("支付结算渠道标识无效")
        _required_text(merchant_account, field="支付结算商户号", max_length=120)
        try:
            normalized_source_kind = (
                source_kind
                if isinstance(source_kind, PaymentSettlementSourceKind)
                else PaymentSettlementSourceKind(source_kind)
            )
        except (TypeError, ValueError) as exc:
            raise ConflictError("支付结算来源类型无效") from exc
        if _SHA256.fullmatch(source_document_sha256) is None:
            raise ConflictError("支付结算源文件摘要无效")
        normalized_start = _utc(period_start, field="支付结算批次开始时间")
        normalized_end = _utc(period_end, field="支付结算批次结束时间")
        if normalized_end <= normalized_start:
            raise ConflictError("支付结算批次结束时间必须晚于开始时间")
        normalized_verified_at = _utc(verified_at, field="支付结算文件验证时间")
        if (
            provider_document_id is not None
            and _RESOURCE.fullmatch(provider_document_id) is None
        ):
            raise ConflictError("支付结算渠道文件编号无效")
        _required_text(source_object_key, field="支付结算源对象键", max_length=512)
        _required_text(source_object_version, field="支付结算源对象版本", max_length=160)
        _required_text(verification_method, field="支付结算验证方式", max_length=40)
        _required_text(parser_version, field="支付结算解析器版本", max_length=80)
        if (
            isinstance(source_size_bytes, bool)
            or not isinstance(source_size_bytes, int)
            or source_size_bytes <= 0
        ):
            raise ConflictError("支付结算源文件大小无效")
        if not lines or len(lines) > 10_000:
            raise ConflictError("支付结算导入行数无效")

        line_ids = [line.provider_line_id for line in lines]
        for line in lines:
            cls._validate_line(line, source_kind=normalized_source_kind)
        if len(set(line_ids)) != len(line_ids):
            raise ConflictError("同一支付结算批次包含重复行号")
        normalized_times: dict[str, datetime] = {}
        for line in lines:
            occurred_at = _utc(line.occurred_at)
            if not normalized_start <= occurred_at < normalized_end:
                raise ConflictError("支付结算行不在批次声明期间内")
            normalized_times[line.provider_line_id] = occurred_at

        lines_sha256 = cls._lines_sha256(lines, normalized_times=normalized_times)
        gross_total = sum(line.gross_amount_cents for line in lines)
        fee_total = sum(line.fee_amount_cents for line in lines)
        net_total = sum(line.net_amount_cents for line in lines)
        existing_batch = session.scalar(
            select(PaymentSettlementBatch)
            .where(
                PaymentSettlementBatch.provider == provider,
                PaymentSettlementBatch.merchant_account == merchant_account,
                PaymentSettlementBatch.source_kind == normalized_source_kind,
                PaymentSettlementBatch.source_document_sha256
                == source_document_sha256,
            )
            .with_for_update()
        )
        if existing_batch is None:
            same_lines = session.scalar(
                select(PaymentSettlementBatch.id).where(
                    PaymentSettlementBatch.provider == provider,
                    PaymentSettlementBatch.merchant_account == merchant_account,
                    PaymentSettlementBatch.source_kind == normalized_source_kind,
                    PaymentSettlementBatch.lines_sha256 == lines_sha256,
                    PaymentSettlementBatch.source_document_sha256 != source_document_sha256,
                )
            )
            if same_lines is not None:
                raise ConflictError("同一支付结算事实使用了不同源文件摘要")
            batch = PaymentSettlementBatch(
                provider=provider,
                merchant_account=merchant_account,
                source_kind=normalized_source_kind,
                period_start=normalized_start,
                period_end=normalized_end,
                provider_document_id=provider_document_id,
                source_document_sha256=source_document_sha256,
                source_document_bytes=source_document_bytes,
                source_object_key=source_object_key,
                source_object_version=source_object_version,
                source_size_bytes=source_size_bytes,
                verification_method=verification_method,
                verified_at=normalized_verified_at,
                parser_version=parser_version,
                lines_sha256=lines_sha256,
                currency="CNY",
                line_count=len(lines),
                gross_total_cents=gross_total,
                fee_total_cents=fee_total,
                net_total_cents=net_total,
            )
            session.add(batch)
            session.flush()
            created_batch = True
        else:
            batch = existing_batch
            created_batch = False
            if (
                _stored_utc(batch.period_start) != normalized_start
                or _stored_utc(batch.period_end) != normalized_end
                or batch.provider_document_id != provider_document_id
                or batch.source_object_key != source_object_key
                or batch.source_object_version != source_object_version
                or batch.source_size_bytes != source_size_bytes
                or batch.source_document_bytes != source_document_bytes
                or batch.verification_method != verification_method
                or batch.parser_version != parser_version
                or batch.lines_sha256 != lines_sha256
                or batch.line_count != len(lines)
                or batch.gross_total_cents != gross_total
                or batch.fee_total_cents != fee_total
                or batch.net_total_cents != net_total
            ):
                raise ConflictError("支付结算源文件摘要对应了不同的不可变批次")

        existing_rows = list(
            session.scalars(
                select(PaymentSettlementEntry)
                .where(PaymentSettlementEntry.batch_id == batch.id)
                .order_by(PaymentSettlementEntry.provider_line_id)
                .with_for_update()
            ).all()
        )
        if not created_batch and {
            row.provider_line_id for row in existing_rows
        } != set(line_ids):
            raise ConflictError("支付结算批次已经封存，不能追加或删减行")

        imported: list[PaymentSettlementEntry] = []
        created_count = 0
        for line in lines:
            occurred_at = normalized_times[line.provider_line_id]
            existing = session.scalar(
                select(PaymentSettlementEntry)
                .where(
                    PaymentSettlementEntry.provider == provider,
                    PaymentSettlementEntry.merchant_account == merchant_account,
                    PaymentSettlementEntry.provider_line_id == line.provider_line_id,
                )
                .with_for_update()
            )
            if existing is not None:
                if (
                    existing.batch_id != batch.id
                    or existing.provider_transaction_id
                    != line.provider_transaction_id
                    or existing.related_provider_reference
                    != line.related_provider_reference
                    or existing.line_type != line.line_type
                    or existing.gross_amount_cents != line.gross_amount_cents
                    or existing.fee_amount_cents != line.fee_amount_cents
                    or existing.net_amount_cents != line.net_amount_cents
                    or existing.currency != line.currency
                    or _stored_utc(existing.occurred_at) != occurred_at
                    or existing.source_document_sha256
                    != source_document_sha256
                ):
                    raise ConflictError("支付结算行号对应了不同的不可变事实")
                imported.append(existing)
                continue
            entry = PaymentSettlementEntry(
                batch_id=batch.id,
                provider=provider,
                merchant_account=merchant_account,
                provider_line_id=line.provider_line_id,
                provider_transaction_id=line.provider_transaction_id,
                related_provider_reference=line.related_provider_reference,
                line_type=line.line_type,
                gross_amount_cents=line.gross_amount_cents,
                fee_amount_cents=line.fee_amount_cents,
                net_amount_cents=line.net_amount_cents,
                currency=line.currency,
                occurred_at=occurred_at,
                source_document_sha256=source_document_sha256,
            )
            session.add(entry)
            session.flush()
            imported.append(entry)
            created_count += 1

        ordered_entries = tuple(
            sorted(imported, key=lambda entry: entry.provider_line_id)
        )
        if len(ordered_entries) != batch.line_count:
            raise ConflictError("支付结算批次控制行数与持久化事实不一致")
        return PaymentSettlementImportResult(
            batch=batch,
            entries=ordered_entries,
            created_count=created_count,
            created_batch=created_batch,
            manifest=cls._manifest(batch, ordered_entries),
        )

    @staticmethod
    def _lines_sha256(
        lines: Sequence[PaymentSettlementLine],
        *,
        normalized_times: dict[str, datetime],
    ) -> str:
        payload = [
            {
                "provider_line_id": line.provider_line_id,
                "provider_transaction_id": line.provider_transaction_id,
                "related_provider_reference": line.related_provider_reference,
                "line_type": line.line_type,
                "gross_amount_cents": line.gross_amount_cents,
                "fee_amount_cents": line.fee_amount_cents,
                "net_amount_cents": line.net_amount_cents,
                "currency": line.currency,
                "occurred_at": normalized_times[line.provider_line_id].isoformat(),
            }
            for line in sorted(lines, key=lambda item: item.provider_line_id)
        ]
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _manifest(
        batch: PaymentSettlementBatch,
        entries: Sequence[PaymentSettlementEntry],
    ) -> PaymentSettlementBatchManifest:
        line_type_counts: dict[str, int] = {}
        for entry in entries:
            line_type_counts[entry.line_type] = (
                line_type_counts.get(entry.line_type, 0) + 1
            )
        return PaymentSettlementBatchManifest(
            batch_id=batch.id,
            provider=batch.provider,
            merchant_account=batch.merchant_account,
            source_kind=batch.source_kind.value,
            period_start=_stored_utc(batch.period_start),
            period_end=_stored_utc(batch.period_end),
            source_document_sha256=batch.source_document_sha256,
            lines_sha256=batch.lines_sha256,
            line_count=batch.line_count,
            line_type_counts=dict(sorted(line_type_counts.items())),
            gross_amount_cents=batch.gross_total_cents,
            fee_amount_cents=batch.fee_total_cents,
            net_amount_cents=batch.net_total_cents,
        )

    @staticmethod
    def _validate_line(
        line: PaymentSettlementLine,
        *,
        source_kind: PaymentSettlementSourceKind,
    ) -> None:
        if (
            not isinstance(line.provider_line_id, str)
            or _RESOURCE.fullmatch(line.provider_line_id) is None
        ):
            raise ConflictError("支付结算行号无效")
        if not isinstance(line.line_type, str) or line.line_type not in _LINE_TYPES:
            raise ConflictError("支付结算行类型无效")
        if source_kind == PaymentSettlementSourceKind.PSP_STATEMENT:
            if line.line_type not in _PSP_LINE_TYPES:
                raise ConflictError("PSP 结算单不能包含银行入账行")
        elif line.line_type != "bank_deposit":
            raise ConflictError("银行流水批次只能包含银行入账行")
        if line.currency != "CNY":
            raise ConflictError("支付结算目前只支持 CNY")
        for value in (
            line.gross_amount_cents,
            line.fee_amount_cents,
            line.net_amount_cents,
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConflictError("支付结算金额必须是整数分")
        if line.fee_amount_cents < 0:
            raise ConflictError("支付结算手续费不能小于 0")
        if line.net_amount_cents != line.gross_amount_cents - line.fee_amount_cents:
            raise ConflictError("支付结算净额无法由总额与手续费对账")
        if (
            not isinstance(line.provider_transaction_id, str)
            or _RESOURCE.fullmatch(line.provider_transaction_id) is None
        ):
            raise ConflictError("支付结算行缺少渠道参考编号")
        if (
            line.related_provider_reference is not None
            and (
                not isinstance(line.related_provider_reference, str)
                or _RESOURCE.fullmatch(line.related_provider_reference) is None
            )
        ):
            raise ConflictError("支付结算关联渠道参考编号无效")
        if line.line_type in _TRANSACTIONAL_TYPES:
            expected_positive = line.line_type in {"capture", "dispute_reversal"}
            if expected_positive != (line.gross_amount_cents > 0):
                raise ConflictError("支付交易结算行金额方向无效")
        elif line.line_type == "fee":
            if (
                line.gross_amount_cents != 0
                or line.fee_amount_cents <= 0
                or line.net_amount_cents != -line.fee_amount_cents
            ):
                raise ConflictError("支付结算手续费行金额形状无效")
        elif (
            line.gross_amount_cents <= 0
            or line.fee_amount_cents != 0
            or line.net_amount_cents != line.gross_amount_cents
        ):
            raise ConflictError("支付结算出款或银行入账行金额形状无效")
        if line.line_type == "bank_deposit" and not line.related_provider_reference:
            raise ConflictError("银行入账行必须关联 PSP 出款编号")
        _utc(line.occurred_at)


@dataclass(frozen=True, slots=True)
class ProviderCostStatementLineInput:
    provider_line_id: str
    provider_job_reference: str
    channel_key: str | None
    task_id: str | None
    amount_cents: int
    currency: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderCostStatementImportResult:
    batch: ProviderCostStatementBatch
    lines: tuple[ProviderCostStatementLine, ...]
    created_count: int
    created_batch: bool


class ProviderCostStatementImportService:
    """Import one immutable supplier invoice/usage-statement batch."""

    @classmethod
    def import_document(
        cls,
        session: Session,
        *,
        supplier: str,
        supplier_account: str,
        period_start: datetime,
        period_end: datetime,
        provider_document_id: str | None,
        source_document_sha256: str,
        source_document_bytes: bytes,
        source_object_key: str,
        source_object_version: str,
        source_size_bytes: int,
        parser_version: str,
    ) -> ProviderCostStatementImportResult:
        actual_sha256, actual_size = _verify_source_bytes(
            source_document_bytes,
            expected_sha256=source_document_sha256,
            expected_size_bytes=source_size_bytes,
            label="供应商账单",
        )
        if parser_version != _PROVIDER_COST_DOCUMENT_PARSER:
            raise ConflictError("供应商账单解析器版本不受支持")
        document = _load_source_document(source_document_bytes, label="供应商账单")
        _exact_keys(
            document,
            {
                "schema_version",
                "supplier",
                "supplier_account",
                "period_start",
                "period_end",
                "lines",
            },
            label="供应商账单原始文件",
        )
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ConflictError("供应商账单原始文件 schema_version 不受支持")
        normalized_start = _utc(period_start, field="供应商账单开始时间")
        normalized_end = _utc(period_end, field="供应商账单结束时间")
        if (
            document["supplier"] != supplier
            or document["supplier_account"] != supplier_account
            or _document_datetime(
                document["period_start"], field="供应商账单文件 period_start"
            )
            != normalized_start
            or _document_datetime(
                document["period_end"], field="供应商账单文件 period_end"
            )
            != normalized_end
        ):
            raise ConflictError("供应商账单文件头与导入作用域不一致")
        raw_lines = document["lines"]
        if not isinstance(raw_lines, list):
            raise ConflictError("供应商账单文件 lines 必须是数组")
        expected_line_keys = {
            "provider_line_id",
            "provider_job_reference",
            "channel_key",
            "task_id",
            "amount_cents",
            "currency",
            "occurred_at",
        }
        lines: list[ProviderCostStatementLineInput] = []
        for index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, dict):
                raise ConflictError(f"供应商账单文件第 {index + 1} 行必须是对象")
            _exact_keys(
                raw_line,
                expected_line_keys,
                label=f"供应商账单文件第 {index + 1} 行",
            )
            lines.append(
                ProviderCostStatementLineInput(
                    provider_line_id=raw_line["provider_line_id"],
                    provider_job_reference=raw_line["provider_job_reference"],
                    channel_key=raw_line["channel_key"],
                    task_id=raw_line["task_id"],
                    amount_cents=raw_line["amount_cents"],
                    currency=raw_line["currency"],
                    occurred_at=_document_datetime(
                        raw_line["occurred_at"],
                        field=f"供应商账单文件第 {index + 1} 行 occurred_at",
                    ),
                )
            )
        return cls._persist_lines(
            session,
            supplier=supplier,
            supplier_account=supplier_account,
            period_start=normalized_start,
            period_end=normalized_end,
            provider_document_id=provider_document_id,
            source_document_sha256=actual_sha256,
            source_document_bytes=source_document_bytes,
            source_object_key=source_object_key,
            source_object_version=source_object_version,
            source_size_bytes=actual_size,
            verification_method=_UPLOAD_VERIFICATION_METHOD,
            verified_at=datetime.now(timezone.utc),
            parser_version=parser_version,
            lines=lines,
        )

    @classmethod
    @_sealed_import
    def _persist_lines(
        cls,
        session: Session,
        *,
        supplier: str,
        supplier_account: str,
        period_start: datetime,
        period_end: datetime,
        provider_document_id: str | None,
        source_document_sha256: str,
        source_document_bytes: bytes,
        source_object_key: str,
        source_object_version: str,
        source_size_bytes: int,
        verification_method: str,
        verified_at: datetime,
        parser_version: str,
        lines: list[ProviderCostStatementLineInput],
    ) -> ProviderCostStatementImportResult:
        _required_text(supplier, field="供应商标识", max_length=120)
        _required_text(supplier_account, field="供应商账户", max_length=160)
        start = _utc(period_start, field="供应商账单开始时间")
        end = _utc(period_end, field="供应商账单结束时间")
        if end <= start:
            raise ConflictError("供应商账单结束时间必须晚于开始时间")
        verified = _utc(verified_at, field="供应商账单验证时间")
        if (
            provider_document_id is not None
            and _RESOURCE.fullmatch(provider_document_id) is None
        ):
            raise ConflictError("供应商账单文件编号无效")
        if _SHA256.fullmatch(source_document_sha256) is None:
            raise ConflictError("供应商账单源文件摘要无效")
        _required_text(source_object_key, field="供应商账单源对象键", max_length=512)
        _required_text(source_object_version, field="供应商账单源对象版本", max_length=160)
        _required_text(verification_method, field="供应商账单验证方式", max_length=40)
        _required_text(parser_version, field="供应商账单解析器版本", max_length=80)
        if (
            isinstance(source_size_bytes, bool)
            or not isinstance(source_size_bytes, int)
            or source_size_bytes <= 0
        ):
            raise ConflictError("供应商账单源文件大小无效")
        if not lines or len(lines) > 100_000:
            raise ConflictError("供应商账单导入行数无效")
        provider_line_ids = [line.provider_line_id for line in lines]
        for provider_line_id in provider_line_ids:
            _required_text(provider_line_id, field="供应商账单行号", max_length=160)
        if len(provider_line_ids) != len(set(provider_line_ids)):
            raise ConflictError("同一供应商账单包含重复行号")

        normalized_times: dict[str, datetime] = {}
        for line in lines:
            _required_text(line.provider_line_id, field="供应商账单行号", max_length=160)
            _required_text(
                line.provider_job_reference,
                field="供应商任务参考编号",
                max_length=160,
            )
            if line.channel_key is not None:
                _required_text(line.channel_key, field="成本通道键", max_length=120)
            if line.task_id is not None:
                _required_text(line.task_id, field="平台任务编号", max_length=36)
            if (
                isinstance(line.amount_cents, bool)
                or not isinstance(line.amount_cents, int)
                or line.amount_cents < 0
            ):
                raise ConflictError("供应商账单金额必须是非负整数分")
            if line.currency != "CNY":
                raise ConflictError("供应商账单目前只支持 CNY")
            occurred_at = _utc(line.occurred_at, field="供应商账单行时间")
            if not start <= occurred_at < end:
                raise ConflictError("供应商账单行不在批次声明期间内")
            normalized_times[line.provider_line_id] = occurred_at

        payload = [
            {
                "provider_line_id": line.provider_line_id,
                "provider_job_reference": line.provider_job_reference,
                "channel_key": line.channel_key,
                "task_id": line.task_id,
                "amount_cents": line.amount_cents,
                "currency": line.currency,
                "occurred_at": normalized_times[line.provider_line_id].isoformat(),
            }
            for line in sorted(lines, key=lambda item: item.provider_line_id)
        ]
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        lines_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        total_cost_cents = sum(line.amount_cents for line in lines)
        existing_batch = session.scalar(
            select(ProviderCostStatementBatch)
            .where(
                ProviderCostStatementBatch.supplier == supplier,
                ProviderCostStatementBatch.supplier_account == supplier_account,
                ProviderCostStatementBatch.source_document_sha256
                == source_document_sha256,
            )
            .with_for_update()
        )
        if existing_batch is None:
            if session.scalar(
                select(ProviderCostStatementBatch.id).where(
                    ProviderCostStatementBatch.supplier == supplier,
                    ProviderCostStatementBatch.supplier_account == supplier_account,
                    ProviderCostStatementBatch.lines_sha256 == lines_sha256,
                    ProviderCostStatementBatch.source_document_sha256 != source_document_sha256,
                )
            ) is not None:
                raise ConflictError("同一供应商成本事实使用了不同源文件摘要")
            batch = ProviderCostStatementBatch(
                supplier=supplier,
                supplier_account=supplier_account,
                period_start=start,
                period_end=end,
                provider_document_id=provider_document_id,
                source_document_sha256=source_document_sha256,
                source_document_bytes=source_document_bytes,
                source_object_key=source_object_key,
                source_object_version=source_object_version,
                source_size_bytes=source_size_bytes,
                verification_method=verification_method,
                verified_at=verified,
                parser_version=parser_version,
                lines_sha256=lines_sha256,
                currency="CNY",
                line_count=len(lines),
                total_cost_cents=total_cost_cents,
            )
            session.add(batch)
            session.flush()
            created_batch = True
        else:
            batch = existing_batch
            created_batch = False
            if (
                _stored_utc(batch.period_start) != start
                or _stored_utc(batch.period_end) != end
                or batch.provider_document_id != provider_document_id
                or batch.source_object_key != source_object_key
                or batch.source_object_version != source_object_version
                or batch.source_size_bytes != source_size_bytes
                or batch.source_document_bytes != source_document_bytes
                or batch.verification_method != verification_method
                or batch.parser_version != parser_version
                or batch.lines_sha256 != lines_sha256
                or batch.line_count != len(lines)
                or batch.total_cost_cents != total_cost_cents
            ):
                raise ConflictError("供应商账单摘要对应了不同不可变批次")

        existing_rows = list(
            session.scalars(
                select(ProviderCostStatementLine)
                .where(ProviderCostStatementLine.batch_id == batch.id)
                .order_by(ProviderCostStatementLine.provider_line_id)
                .with_for_update()
            ).all()
        )
        if not created_batch and {
            row.provider_line_id for row in existing_rows
        } != set(provider_line_ids):
            raise ConflictError("供应商账单批次已经封存，不能追加或删减行")

        imported: list[ProviderCostStatementLine] = []
        created_count = 0
        for line in lines:
            existing = session.scalar(
                select(ProviderCostStatementLine)
                .where(
                    ProviderCostStatementLine.supplier == supplier,
                    ProviderCostStatementLine.supplier_account == supplier_account,
                    ProviderCostStatementLine.provider_line_id
                    == line.provider_line_id,
                )
                .with_for_update()
            )
            occurred_at = normalized_times[line.provider_line_id]
            if existing is not None:
                if (
                    existing.batch_id != batch.id
                    or existing.provider_job_reference
                    != line.provider_job_reference
                    or existing.channel_key != line.channel_key
                    or existing.task_id != line.task_id
                    or existing.amount_cents != line.amount_cents
                    or existing.currency != line.currency
                    or _stored_utc(existing.occurred_at) != occurred_at
                ):
                    raise ConflictError("供应商账单行号对应了不同不可变事实")
                imported.append(existing)
                continue
            row = ProviderCostStatementLine(
                batch_id=batch.id,
                supplier=supplier,
                supplier_account=supplier_account,
                provider_line_id=line.provider_line_id,
                provider_job_reference=line.provider_job_reference,
                channel_key=line.channel_key,
                task_id=line.task_id,
                amount_cents=line.amount_cents,
                currency=line.currency,
                occurred_at=occurred_at,
            )
            session.add(row)
            session.flush()
            imported.append(row)
            created_count += 1
        ordered_rows = tuple(sorted(imported, key=lambda item: item.provider_line_id))
        if len(ordered_rows) != batch.line_count:
            raise ConflictError("供应商账单控制行数与持久化事实不一致")
        return ProviderCostStatementImportResult(
            batch=batch,
            lines=ordered_rows,
            created_count=created_count,
            created_batch=created_batch,
        )


def validate_archived_statement(
    batch: PaymentSettlementBatch | ProviderCostStatementBatch,
) -> None:
    """Reparse the retained bytes and bind the sealed canonical lines to them.

    This verifies content integrity only. No upload, object locator, or caller
    metadata constitutes proof that a PSP, bank, or supplier issued the file.
    """

    payment = isinstance(batch, PaymentSettlementBatch)
    label = "支付结算" if payment else "供应商账单"
    _verify_source_bytes(
        batch.source_document_bytes,
        expected_sha256=batch.source_document_sha256,
        expected_size_bytes=batch.source_size_bytes,
        label=label,
    )
    expected_parser = _PAYMENT_DOCUMENT_PARSER if payment else _PROVIDER_COST_DOCUMENT_PARSER
    if batch.parser_version != expected_parser:
        raise ConflictError(f"{label}存档解析器版本不受支持")
    document = _load_source_document(batch.source_document_bytes, label=label)
    headers = (
        {
            "provider": batch.provider,
            "merchant_account": batch.merchant_account,
            "source_kind": batch.source_kind.value,
        }
        if payment
        else {"supplier": batch.supplier, "supplier_account": batch.supplier_account}
    )
    _exact_keys(
        document,
        {"schema_version", "period_start", "period_end", "lines", *headers},
        label=f"{label}存档",
    )
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or any(document[key] != value for key, value in headers.items())
        or _document_datetime(document["period_start"], field=f"{label}存档开始时间")
        != _stored_utc(batch.period_start)
        or _document_datetime(document["period_end"], field=f"{label}存档结束时间")
        != _stored_utc(batch.period_end)
    ):
        raise ConflictError(f"{label}存档文件头与批次不一致")
    raw_lines = document["lines"]
    if not isinstance(raw_lines, list) or len(raw_lines) != batch.line_count:
        raise ConflictError(f"{label}存档控制行数不一致")
    expected_line_keys = (
        {
            "provider_line_id", "provider_transaction_id", "related_provider_reference",
            "line_type", "gross_amount_cents", "fee_amount_cents", "net_amount_cents",
            "currency", "occurred_at",
        }
        if payment
        else {
            "provider_line_id", "provider_job_reference", "channel_key", "task_id",
            "amount_cents", "currency", "occurred_at",
        }
    )
    normalized_lines = []
    for raw_line in raw_lines:
        if not isinstance(raw_line, dict):
            raise ConflictError(f"{label}存档行无效")
        _exact_keys(raw_line, expected_line_keys, label=f"{label}存档行")
        _required_text(raw_line["provider_line_id"], field=f"{label}存档行号", max_length=160)
        occurred_at = _document_datetime(raw_line["occurred_at"], field=f"{label}存档行时间")
        if not _stored_utc(batch.period_start) <= occurred_at < _stored_utc(batch.period_end):
            raise ConflictError(f"{label}存档行不在批次期间内")
        normalized_lines.append({**raw_line, "occurred_at": occurred_at.isoformat()})
    try:
        canonical = json.dumps(
            sorted(normalized_lines, key=lambda line: line["provider_line_id"]),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConflictError(f"{label}存档行不能规范化") from exc
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != batch.lines_sha256:
        raise ConflictError(f"{label}存档字节解析结果与封存行摘要不一致")
