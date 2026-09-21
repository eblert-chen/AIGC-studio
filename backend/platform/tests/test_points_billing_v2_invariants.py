from __future__ import annotations

from datetime import timedelta
import uuid

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyMembership,
    CompanyModelGrant,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointPriceVersion,
    CompanyPointWalletAccount,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    PersonalLedgerEntry,
    PersonalWalletAccount,
    PersonalWorkspace,
    PointLedgerKind,
    PointLotSourceKind,
    PointPriceVersionStatus,
    EnterpriseContractStatus,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    UserAccountType,
    WalletAccount,
    ModelDefinition,
    utcnow,
)
from platform_api.services.admin_analytics import AdminAnalyticsService
from platform_api.services.company_points_billing import (
    CompanyPointBillingService,
    cents_to_points_migration,
)
from platform_api.services.errors import ConflictError
from platform_api.services.models import ModelGrantService
from platform_api.services.personal_billing import PersonalWalletService
from platform_api.services.quote_revision import model_grant_quote_revision
from platform_api.services.tasks import TaskService


def _seed_company(session, *, available_cents: int) -> tuple[Company, User, ModelDefinition]:
    suffix = uuid.uuid4().hex
    company = Company(name=f"Points v2 {suffix}")
    user = User(
        email=f"points-v2-{suffix}@example.test",
        display_name="Points v2 user",
        account_type=UserAccountType.COMPANY,
    )
    model = ModelDefinition(
        slug=f"points-v2-{suffix}",
        display_name="Points v2 model",
        provider_key="points-v2-test",
        billing_mode="per_item",
    )
    session.add_all([company, user, model])
    session.flush()
    session.add(CompanyMembership(company_id=company.id, user_id=user.id))
    session.add(
        WalletAccount(
            company_id=company.id,
            available_cents=available_cents,
            reserved_cents=0,
        )
    )
    if available_cents:
        session.add(
            LedgerEntry(
                company_id=company.id,
                kind=LedgerKind.RECHARGE,
                amount_cents=available_cents,
                available_delta_cents=available_cents,
                reserved_delta_cents=0,
                idempotency_key=f"points-v2-opening-{suffix}",
                task_id=None,
                note="legacy wallet projection evidence",
            )
        )
    session.flush()
    return company, user, model


def _point_task(
    *,
    company: Company,
    user: User,
    model: ModelDefinition,
    quote_points: int,
    key: str,
) -> GenerationTask:
    return GenerationTask(
        company_id=company.id,
        personal_workspace_id=None,
        user_id=user.id,
        model_id=model.id,
        idempotency_key=key,
        request_fingerprint=(key.encode("utf-8").hex() + ("0" * 64))[:64],
        status=TaskStatus.DRAFT,
        request_payload={"prompt": key, "output_count": 1},
        billing_unit=BillingUnit.POINT,
        billing_version=2,
        quote_cents=None,
        quote_points=quote_points,
        pricing_snapshot={
            "schema_version": 2,
            "billing_unit": BillingUnit.POINT.value,
            "billing_version": 2,
            "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
            "quote_points": quote_points,
        },
        capability_snapshot={},
        reserved_cents=0,
        reserved_points=0,
        actual_cost_cents=None,
        actual_cost_points=None,
    )


def _assert_company_point_conservation(session, company_id: str) -> None:
    wallet = session.get(CompanyPointWalletAccount, company_id)
    assert wallet is not None
    lots = list(
        session.scalars(
            select(CompanyPointLot).where(CompanyPointLot.company_id == company_id)
        ).all()
    )
    allocations = list(
        session.scalars(
            select(TaskPointLotAllocation).where(
                TaskPointLotAllocation.company_id == company_id
            )
        ).all()
    )
    assert wallet.available_points == sum(lot.available_points for lot in lots)
    assert wallet.reserved_points == sum(lot.reserved_points for lot in lots)
    for lot in lots:
        assert lot.original_points == (
            lot.available_points + lot.reserved_points + lot.settled_points
        )
    for allocation in allocations:
        assert allocation.allocated_points == (
            allocation.reserved_points
            + allocation.settled_points
            + allocation.released_points
        )


@pytest.mark.parametrize(
    (
        "cents",
        "points",
        "legacy_points",
        "remainder_cents",
        "rounding_points",
        "subsidy_cents",
    ),
    [
        (0, 0, 0, 0, 0, 0),
        (10, 1, 1, 0, 0, 0),
        (12_340, 1_234, 1_234, 0, 0, 0),
        (12_345, 1_235, 1_234, 5, 1, 5),
        (9, 1, 0, 9, 1, 1),
        (1, 1, 0, 1, 1, 9),
    ],
)
def test_ten_cents_per_point_conversion_preserves_remainder_evidence(
    cents: int,
    points: int,
    legacy_points: int,
    remainder_cents: int,
    rounding_points: int,
    subsidy_cents: int,
) -> None:
    converted = cents_to_points_migration(cents)
    assert converted.source_cents == cents
    assert converted.converted_points == points
    assert converted.legacy_points == legacy_points
    assert converted.rounding_remainder_cents == remainder_cents
    assert converted.rounding_grant_points == rounding_points
    assert converted.rounding_subsidy_cents == subsidy_cents
    assert cents + subsidy_cents == points * 10


def test_cents_conversion_rejects_negative_and_boolean_inputs() -> None:
    for invalid in (-1, True, False):
        with pytest.raises(ConflictError):
            cents_to_points_migration(invalid)


def test_company_migration_is_blocked_by_any_inflight_legacy_task(app) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=1_000)
        task = GenerationTask(
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user.id,
            model_id=model.id,
            idempotency_key="legacy-inflight",
            request_fingerprint="a" * 64,
            status=TaskStatus.QUEUED,
            request_payload={"prompt": "legacy"},
            billing_unit=BillingUnit.CNY_CENT,
            billing_version=1,
            quote_cents=100,
            quote_points=None,
            pricing_snapshot={
                "schema_version": 1,
                "billing_unit": BillingUnit.CNY_CENT.value,
                "billing_version": 1,
            },
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=None,
            actual_cost_points=None,
        )
        session.add(task)
        session.flush()
        company_id = company.id

    with app.state.session_factory() as session:
        with pytest.raises(ConflictError):
            CompanyPointBillingService.migrate(
                session,
                company_id=company_id,
                expected_available_cents=1_000,
                idempotency_key="blocked-migration",
            )
        session.rollback()

    with app.state.session_factory() as session:
        company = session.get(Company, company_id)
        assert company is not None and company.billing_version == 1
        assert session.get(CompanyPointWalletAccount, company_id) is None
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        ) == 0


def test_company_migration_is_blocked_by_unresolved_relay_submission(app) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=100)
        task = GenerationTask(
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user.id,
            model_id=model.id,
            idempotency_key="legacy-terminal-with-unknown-relay",
            request_fingerprint="u" * 64,
            status=TaskStatus.FAILED,
            request_payload={"prompt": "legacy terminal"},
            billing_unit=BillingUnit.CNY_CENT,
            billing_version=1,
            quote_cents=10,
            quote_points=None,
            pricing_snapshot={"schema_version": 1},
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=None,
            actual_cost_points=None,
        )
        session.add(task)
        session.flush()
        session.add_all(
            [
                LedgerEntry(
                    company_id=company.id,
                    kind=LedgerKind.RESERVE,
                    amount_cents=10,
                    available_delta_cents=-10,
                    reserved_delta_cents=10,
                    idempotency_key="legacy-unknown-relay-reserve",
                    task_id=task.id,
                ),
                LedgerEntry(
                    company_id=company.id,
                    kind=LedgerKind.RELEASE,
                    amount_cents=10,
                    available_delta_cents=10,
                    reserved_delta_cents=-10,
                    idempotency_key="legacy-unknown-relay-release",
                    task_id=task.id,
                ),
                RelaySubmissionOutbox(
                    company_id=company.id,
                    personal_workspace_id=None,
                    task_id=task.id,
                    status=RelayOutboxStatus.RECONCILIATION_REQUIRED,
                    idempotency_key="legacy-unknown-relay-outbox",
                    relay_payload={"client_reference_id": task.id},
                ),
            ]
        )
        company_id = company.id

    with app.state.session_factory() as session:
        with pytest.raises(ConflictError, match="Relay|对账"):
            CompanyPointBillingService.migrate(
                session,
                company_id=company_id,
                expected_available_cents=100,
                idempotency_key="blocked-by-relay-unknown",
            )
        session.rollback()


def test_company_migration_fails_closed_when_wallet_and_ledger_do_not_reconcile(
    app,
) -> None:
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=100)
        wallet = session.get(WalletAccount, company.id)
        assert wallet is not None
        # Simulate historical projection drift.  Migration must never invent
        # an opening point lot from a wallet that its immutable ledger cannot
        # reproduce.
        wallet.available_cents = 101
        company_id = company.id

    with app.state.session_factory() as session:
        with pytest.raises(ConflictError, match="账本|对账"):
            CompanyPointBillingService.migrate(
                session,
                company_id=company_id,
                expected_available_cents=101,
                idempotency_key="unreconciled-migration",
            )
        session.rollback()

    with app.state.session_factory() as session:
        company = session.get(Company, company_id)
        assert company is not None and company.billing_version == 1
        assert session.get(CompanyPointWalletAccount, company_id) is None
        assert session.scalar(
            select(func.count(CompanyPointLot.id)).where(
                CompanyPointLot.company_id == company_id
            )
        ) == 0


def test_company_migration_rejects_failed_task_with_a_charge_even_when_wallet_balances(
    app,
) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=100)
        task = GenerationTask(
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user.id,
            model_id=model.id,
            idempotency_key="legacy-failed-charged",
            request_fingerprint="f" * 64,
            status=TaskStatus.FAILED,
            request_payload={"prompt": "must not charge"},
            billing_unit=BillingUnit.CNY_CENT,
            billing_version=1,
            quote_cents=10,
            quote_points=None,
            pricing_snapshot={"schema_version": 1},
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=5,
            actual_cost_points=None,
        )
        session.add(task)
        session.flush()
        session.add_all(
            [
                LedgerEntry(
                    company_id=company.id,
                    kind=LedgerKind.RESERVE,
                    amount_cents=10,
                    available_delta_cents=-10,
                    reserved_delta_cents=10,
                    idempotency_key="legacy-failed-reserve",
                    task_id=task.id,
                ),
                LedgerEntry(
                    company_id=company.id,
                    kind=LedgerKind.RELEASE,
                    amount_cents=10,
                    available_delta_cents=10,
                    reserved_delta_cents=-10,
                    idempotency_key="legacy-failed-release",
                    task_id=task.id,
                ),
            ]
        )
        company_id = company.id

    with app.state.session_factory() as session:
        with pytest.raises(ConflictError, match="失败|取消|结算"):
            CompanyPointBillingService.migrate(
                session,
                company_id=company_id,
                expected_available_cents=100,
                idempotency_key="reject-failed-charge",
            )
        session.rollback()


def test_company_migration_rejects_cross_task_ledger_swaps_with_matching_totals(
    app,
) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=100)
        wallet = session.get(WalletAccount, company.id)
        assert wallet is not None
        wallet.available_cents = 70
        tasks = []
        for suffix, quote in (("one", 10), ("two", 20)):
            task = GenerationTask(
                company_id=company.id,
                personal_workspace_id=None,
                user_id=user.id,
                model_id=model.id,
                idempotency_key=f"legacy-swap-{suffix}",
                request_fingerprint=(suffix[0] * 64),
                status=TaskStatus.SUCCEEDED,
                request_payload={"prompt": suffix},
                billing_unit=BillingUnit.CNY_CENT,
                billing_version=1,
                quote_cents=quote,
                quote_points=None,
                pricing_snapshot={"schema_version": 1},
                capability_snapshot={},
                reserved_cents=0,
                reserved_points=0,
                actual_cost_cents=quote,
                actual_cost_points=None,
            )
            session.add(task)
            tasks.append(task)
        session.flush()
        for task, wrong_amount in ((tasks[0], 20), (tasks[1], 10)):
            session.add_all(
                [
                    LedgerEntry(
                        company_id=company.id,
                        kind=LedgerKind.RESERVE,
                        amount_cents=wrong_amount,
                        available_delta_cents=-wrong_amount,
                        reserved_delta_cents=wrong_amount,
                        idempotency_key=f"legacy-swap-reserve-{task.id}",
                        task_id=task.id,
                    ),
                    LedgerEntry(
                        company_id=company.id,
                        kind=LedgerKind.SETTLE,
                        amount_cents=wrong_amount,
                        available_delta_cents=0,
                        reserved_delta_cents=-wrong_amount,
                        idempotency_key=f"legacy-swap-settle-{task.id}",
                        task_id=task.id,
                    ),
                ]
            )
        company_id = company.id

    with app.state.session_factory() as session:
        with pytest.raises(ConflictError, match="预占|分录|对账"):
            CompanyPointBillingService.migrate(
                session,
                company_id=company_id,
                expected_available_cents=70,
                idempotency_key="reject-cross-task-swap",
            )
        session.rollback()


def test_company_migration_creates_auditable_lots_and_is_idempotent(app) -> None:
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=12_345)
        company_id = company.id

    with app.state.session_factory.begin() as session:
        result = CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=12_345,
            idempotency_key="migration-12345",
        )
        assert result.changed is True
        assert result.wallet.available_points == 1_235

    with app.state.session_factory() as session:
        company = session.get(Company, company_id)
        cents_wallet = session.get(WalletAccount, company_id)
        point_wallet = session.get(CompanyPointWalletAccount, company_id)
        lots = list(
            session.scalars(
                select(CompanyPointLot)
                .where(CompanyPointLot.company_id == company_id)
                .order_by(CompanyPointLot.source_kind)
            ).all()
        )
        assert company is not None and company.billing_version == 2
        assert cents_wallet is not None
        assert (cents_wallet.available_cents, cents_wallet.reserved_cents) == (12_345, 0)
        assert point_wallet is not None
        assert (
            point_wallet.available_points,
            point_wallet.reserved_points,
            point_wallet.migrated_from_available_cents,
            point_wallet.migration_remainder_cents,
            point_wallet.migration_rounding_grant_points,
        ) == (1_235, 0, 12_345, 5, 1)
        by_source = {lot.source_kind: lot for lot in lots}
        legacy = by_source[PointLotSourceKind.LEGACY]
        rounding = by_source[PointLotSourceKind.MIGRATION_REMAINDER]
        assert (
            legacy.original_points,
            legacy.cash_basis_cents,
            legacy.subsidy_cents,
        ) == (1_234, 12_340, 0)
        assert (
            rounding.original_points,
            rounding.cash_basis_cents,
            rounding.subsidy_cents,
        ) == (1, 5, 5)
        _assert_company_point_conservation(session, company_id)

    with app.state.session_factory.begin() as session:
        replay = CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=12_345,
            idempotency_key="migration-12345",
        )
        assert replay.changed is False


def test_company_migration_hashes_only_overlong_internal_lot_keys(app) -> None:
    migration_key = "m" * 120
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=101)
        company_id = company.id

    with app.state.session_factory.begin() as session:
        result = CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=101,
            idempotency_key=migration_key,
        )
        assert result.wallet.migration_idempotency_key == migration_key

    with app.state.session_factory() as session:
        lots = list(
            session.scalars(
                select(CompanyPointLot)
                .where(CompanyPointLot.company_id == company_id)
                .order_by(CompanyPointLot.source_kind)
            ).all()
        )
        assert len(lots) == 2
        lot_keys = {lot.idempotency_key for lot in lots}
        assert len(lot_keys) == 2
        assert all(len(key) <= 120 for key in lot_keys)
        assert all(key.startswith("migration-lot:") for key in lot_keys)
    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(CompanyPointLot.id)).where(
                CompanyPointLot.company_id == company_id
            )
        ) == 2
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id,
                CompanyPointLedgerEntry.kind == PointLedgerKind.MIGRATION,
            )
        ) == 1


def test_migration_creates_disabled_price_candidates_not_live_prices(app) -> None:
    with app.state.session_factory.begin() as session:
        company, _, model = _seed_company(session, available_cents=100)
        grant = CompanyModelGrant(
            company_id=company.id,
            model_id=model.id,
            enabled=True,
            price_per_second_cents=None,
            price_per_item_cents=25,
        )
        session.add(grant)
        session.flush()
        company_id, grant_id = company.id, grant.id

    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=100,
            idempotency_key="candidate-only-migration",
        )

    with app.state.session_factory() as session:
        grant = session.get(CompanyModelGrant, grant_id)
        assert grant is not None
        assert grant.enabled is False
        assert grant.price_per_second_cents is None
        assert grant.price_per_item_cents is None
        assert grant.price_per_second_points is None
        assert grant.price_per_item_points is None
        assert grant.point_price_candidate_per_second is None
        assert grant.point_price_candidate_per_item == 3
        assert grant.point_price_candidate_revision.startswith("sha256:")
        version = session.get(
            CompanyPointPriceVersion,
            grant.point_price_candidate_version_id,
        )
        assert version is not None
        assert version.status == PointPriceVersionStatus.CANDIDATE
        assert version.unit_price_points == 3
        assert version.source_price_cents == 25


def test_identical_point_price_upsert_reuses_the_active_version(app) -> None:
    with app.state.session_factory.begin() as session:
        company, _, model = _seed_company(session, available_cents=100)
        company_id, model_id = company.id, model.id

    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=100,
            idempotency_key="stable-point-price-migration",
        )

    with app.state.session_factory.begin() as session:
        first = ModelGrantService.upsert_grant(
            session,
            company_id=company_id,
            model_id=model_id,
            enabled=False,
            price_per_second_cents=None,
            price_per_item_cents=None,
            price_per_second_points=None,
            price_per_item_points=17,
            config_override={},
        )
        first_version_id = first.point_price_active_version_id
        first_revision = model_grant_quote_revision(
            model=session.get(ModelDefinition, model_id),
            grant=first,
        )
        repeated = ModelGrantService.upsert_grant(
            session,
            company_id=company_id,
            model_id=model_id,
            enabled=False,
            price_per_second_cents=None,
            price_per_item_cents=None,
            price_per_second_points=None,
            price_per_item_points=17,
            config_override={},
        )
        assert repeated.point_price_active_version_id == first_version_id
        assert model_grant_quote_revision(
            model=session.get(ModelDefinition, model_id),
            grant=repeated,
        ) == first_revision
        assert session.scalar(
            select(func.count(CompanyPointPriceVersion.id)).where(
                CompanyPointPriceVersion.company_id == company_id,
                CompanyPointPriceVersion.status == PointPriceVersionStatus.ACTIVE,
            )
        ) == 1

        changed = ModelGrantService.upsert_grant(
            session,
            company_id=company_id,
            model_id=model_id,
            enabled=False,
            price_per_second_cents=None,
            price_per_item_cents=None,
            price_per_second_points=None,
            price_per_item_points=18,
            config_override={},
        )
        assert changed.point_price_active_version_id != first_version_id
        current_version = session.get(
            CompanyPointPriceVersion,
            changed.point_price_active_version_id,
        )
        assert current_version is not None
        assert current_version.supersedes_version_id == first_version_id


def test_old_cent_task_replay_after_migration_never_touches_point_wallet(app) -> None:
    request_payload = {"prompt": "historical cents replay", "output_count": 1}
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=100)
        task = GenerationTask(
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user.id,
            model_id=model.id,
            idempotency_key="historical-cent-task",
            request_fingerprint=TaskService.request_fingerprint(
                model_id=model.id,
                request_payload=request_payload,
            ),
            status=TaskStatus.SUCCEEDED,
            request_payload=request_payload,
            billing_unit=BillingUnit.CNY_CENT,
            billing_version=1,
            quote_cents=20,
            quote_points=None,
            pricing_snapshot={
                "schema_version": 1,
                "billing_unit": BillingUnit.CNY_CENT.value,
                "billing_version": 1,
                "quote_cents": 20,
            },
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=20,
            actual_cost_points=None,
        )
        session.add(task)
        session.flush()
        wallet = session.get(WalletAccount, company.id)
        assert wallet is not None
        wallet.available_cents = 80
        session.add_all(
            [
                LedgerEntry(
                    company_id=company.id,
                    kind=LedgerKind.RESERVE,
                    amount_cents=20,
                    available_delta_cents=-20,
                    reserved_delta_cents=20,
                    idempotency_key="historical-cent-task-reserve",
                    task_id=task.id,
                ),
                LedgerEntry(
                    company_id=company.id,
                    kind=LedgerKind.SETTLE,
                    amount_cents=20,
                    available_delta_cents=0,
                    reserved_delta_cents=-20,
                    idempotency_key="historical-cent-task-settle",
                    task_id=task.id,
                ),
            ]
        )
        company_id, user_id, model_id, task_id = (
            company.id,
            user.id,
            model.id,
            task.id,
        )

    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=80,
            idempotency_key="historical-replay-migration",
        )

    with app.state.session_factory.begin() as session:
        before_wallet = session.get(CompanyPointWalletAccount, company_id)
        assert before_wallet is not None
        before_balance = (before_wallet.available_points, before_wallet.reserved_points)
        before_ledger_count = session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        )
        replay, created = TaskService.create(
            session,
            company_id=company_id,
            user_id=user_id,
            model_id=model_id,
            request_payload=request_payload,
            idempotency_key="historical-cent-task",
            require_quote_revision=True,
        )
        assert created is False
        assert replay.id == task_id
        assert replay.billing_unit == BillingUnit.CNY_CENT
        assert replay.billing_version == 1
        assert replay.quote_cents == 20
        assert replay.quote_points is None
        after_wallet = session.get(CompanyPointWalletAccount, company_id)
        assert after_wallet is not None
        assert (after_wallet.available_points, after_wallet.reserved_points) == before_balance
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        ) == before_ledger_count
        assert session.scalar(
            select(func.count(TaskPointLotAllocation.id)).where(
                TaskPointLotAllocation.task_id == task_id
            )
        ) == 0


def test_company_migration_does_not_scale_or_relabel_personal_points(app) -> None:
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=101)
        suffix = uuid.uuid4().hex
        personal_user = User(
            email=f"personal-preserved-{suffix}@example.test",
            display_name="Personal preserved",
            account_type=UserAccountType.PERSONAL,
        )
        session.add(personal_user)
        session.flush()
        workspace = PersonalWorkspace(user_id=personal_user.id, active=True)
        session.add(workspace)
        session.flush()
        session.add(
            PersonalWalletAccount(
                workspace_id=workspace.id,
                available_points=137,
                reserved_points=3,
            )
        )
        session.add(
            PersonalLedgerEntry(
                workspace_id=workspace.id,
                kind="RECHARGE",
                amount_points=140,
                available_delta_points=140,
                reserved_delta_points=0,
                idempotency_key="personal-opening",
                task_id=None,
                note="legacy personal points",
            )
        )
        company_id = company.id
        workspace_id = workspace.id

    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=101,
            idempotency_key="company-only-migration",
        )

    with app.state.session_factory() as session:
        personal_wallet = session.get(PersonalWalletAccount, workspace_id)
        personal_entry = session.scalar(
            select(PersonalLedgerEntry).where(
                PersonalLedgerEntry.workspace_id == workspace_id,
                PersonalLedgerEntry.idempotency_key == "personal-opening",
            )
        )
        assert personal_wallet is not None
        assert (personal_wallet.available_points, personal_wallet.reserved_points) == (
            137,
            3,
        )
        assert personal_entry is not None
        assert (
            personal_entry.amount_points,
            personal_entry.available_delta_points,
            personal_entry.reserved_delta_points,
        ) == (140, 140, 0)


def test_points_reserve_settle_release_and_lot_allocations_are_idempotent(app) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=1_000)
        company_id, user_id, model_id = company.id, user.id, model.id
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=1_000,
            idempotency_key="migrate-task-flow",
        )
        success_task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=60,
            key="points-success",
        )
        failure_task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=30,
            key="points-failure",
        )
        session.add_all([success_task, failure_task])
        session.flush()
        success_task_id, failure_task_id = success_task.id, failure_task.id

    with app.state.session_factory.begin() as session:
        first_wallet, first_entry = CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=success_task_id,
            amount_points=60,
            idempotency_key="reserve-success",
        )
        replay_wallet, replay_entry = CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=success_task_id,
            amount_points=60,
            idempotency_key="reserve-success",
        )
        assert replay_entry.id == first_entry.id
        assert replay_wallet.company_id == first_wallet.company_id
        _assert_company_point_conservation(session, company_id)

    with app.state.session_factory.begin() as session:
        first_wallet, first_entry = CompanyPointBillingService.settle_success(
            session,
            company_id=company_id,
            task_id=success_task_id,
            actual_cost_points=60,
            idempotency_key="settle-success",
        )
        replay_wallet, replay_entry = CompanyPointBillingService.settle_success(
            session,
            company_id=company_id,
            task_id=success_task_id,
            actual_cost_points=60,
            idempotency_key="settle-success",
        )
        assert replay_entry.id == first_entry.id
        assert replay_wallet.company_id == first_wallet.company_id
        _assert_company_point_conservation(session, company_id)

    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=failure_task_id,
            amount_points=30,
            idempotency_key="reserve-failure",
        )
        first_wallet, first_entry = CompanyPointBillingService.release_failure(
            session,
            company_id=company_id,
            task_id=failure_task_id,
            idempotency_key="release-failure",
            failure_reason="provider failed",
        )
        replay_wallet, replay_entry = CompanyPointBillingService.release_failure(
            session,
            company_id=company_id,
            task_id=failure_task_id,
            idempotency_key="release-failure",
            failure_reason="provider failed",
        )
        assert replay_entry.id == first_entry.id
        assert replay_wallet.company_id == first_wallet.company_id
        with pytest.raises(ConflictError, match="幂等键|意图"):
            CompanyPointBillingService.release_failure(
                session,
                company_id=company_id,
                task_id=failure_task_id,
                idempotency_key="release-failure",
                failure_reason="a different provider failure",
            )
        with pytest.raises(ConflictError, match="终态"):
            CompanyPointBillingService.release_failure(
                session,
                company_id=company_id,
                task_id=failure_task_id,
                idempotency_key="release-failure",
                failure_reason="provider failed",
                terminal_status=TaskStatus.CANCELLED,
            )
        _assert_company_point_conservation(session, company_id)

    with app.state.session_factory() as session:
        wallet = session.get(CompanyPointWalletAccount, company_id)
        success_task = session.get(GenerationTask, success_task_id)
        failure_task = session.get(GenerationTask, failure_task_id)
        assert wallet is not None
        assert (wallet.available_points, wallet.reserved_points) == (40, 0)
        assert success_task is not None
        assert (
            success_task.status,
            success_task.actual_cost_points,
            success_task.actual_cost_cents,
        ) == (TaskStatus.SUCCEEDED, 60, None)
        assert failure_task is not None
        assert (
            failure_task.status,
            failure_task.actual_cost_points,
            failure_task.reserved_points,
        ) == (TaskStatus.FAILED, None, 0)
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        ) == 5
        _assert_company_point_conservation(session, company_id)
        assert (user_id, model_id) == (success_task.user_id, success_task.model_id)


def test_mixed_lots_release_back_to_their_original_sources(app) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=20)
        company_id = company.id
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=20,
            idempotency_key="mixed-migration",
        )
        CompanyPointBillingService.credit(
            session,
            company_id=company_id,
            amount_points=3,
            source_kind=PointLotSourceKind.PROMOTIONAL,
            cash_basis_cents=0,
            subsidy_cents=30,
            idempotency_key="mixed-promo",
            note="promotion",
            expires_at=None,
        )
        task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=4,
            key="mixed-release",
        )
        session.add(task)
        session.flush()
        task_id = task.id

    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=task_id,
            amount_points=4,
            idempotency_key="mixed-reserve",
        )
        allocations = list(
            session.scalars(
                select(TaskPointLotAllocation).where(
                    TaskPointLotAllocation.task_id == task_id
                )
            ).all()
        )
        assert sorted(allocation.allocated_points for allocation in allocations) == [2, 2]
        _assert_company_point_conservation(session, company_id)
        CompanyPointBillingService.release_failure(
            session,
            company_id=company_id,
            task_id=task_id,
            idempotency_key="mixed-release-ledger",
            failure_reason="safe failure",
        )
        _assert_company_point_conservation(session, company_id)

    with app.state.session_factory() as session:
        lots = list(
            session.scalars(
                select(CompanyPointLot).where(CompanyPointLot.company_id == company_id)
            ).all()
        )
        by_source = {lot.source_kind: lot for lot in lots}
        assert by_source[PointLotSourceKind.LEGACY].available_points == 2
        assert by_source[PointLotSourceKind.PROMOTIONAL].available_points == 3
        assert all(lot.reserved_points == 0 for lot in lots)
        allocations = list(
            session.scalars(
                select(TaskPointLotAllocation).where(
                    TaskPointLotAllocation.task_id == task_id
                )
            ).all()
        )
        assert all(allocation.reserved_points == 0 for allocation in allocations)
        assert sum(allocation.released_points for allocation in allocations) == 4


def test_company_and_personal_idempotency_domains_do_not_collide(app) -> None:
    shared_key = "shared-company-personal-credit"
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=0)
        CompanyPointBillingService.migrate(
            session,
            company_id=company.id,
            expected_available_cents=0,
            idempotency_key="cross-scope-migration",
        )
        suffix = uuid.uuid4().hex
        personal_user = User(
            email=f"cross-scope-{suffix}@example.test",
            display_name="Cross scope",
            account_type=UserAccountType.PERSONAL,
        )
        session.add(personal_user)
        session.flush()
        workspace = PersonalWorkspace(user_id=personal_user.id, active=True)
        session.add(workspace)
        session.flush()
        session.add(
            PersonalWalletAccount(
                workspace_id=workspace.id,
                available_points=0,
                reserved_points=0,
            )
        )
        session.flush()
        company_id, workspace_id = company.id, workspace.id

        CompanyPointBillingService.credit(
            session,
            company_id=company_id,
            amount_points=7,
            source_kind=PointLotSourceKind.PROMOTIONAL,
            cash_basis_cents=0,
            subsidy_cents=70,
            idempotency_key=shared_key,
            note="company",
        )
        PersonalWalletService.credit(
            session,
            workspace_id=workspace_id,
            amount_points=11,
            idempotency_key=shared_key,
            note="personal",
        )

    with app.state.session_factory() as session:
        company_wallet = session.get(CompanyPointWalletAccount, company_id)
        personal_wallet = session.get(PersonalWalletAccount, workspace_id)
        assert company_wallet is not None and company_wallet.available_points == 7
        assert personal_wallet is not None and personal_wallet.available_points == 11
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.idempotency_key == shared_key
            )
        ) == 1


def test_point_credit_idempotency_binds_the_complete_financial_intent(app) -> None:
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=0)
        CompanyPointBillingService.migrate(
            session,
            company_id=company.id,
            expected_available_cents=0,
            idempotency_key="credit-intent-migration",
        )
        company_id = company.id
        wallet, first_entry, created = CompanyPointBillingService.credit(
            session,
            company_id=company_id,
            amount_points=2,
            source_kind=PointLotSourceKind.PROMOTIONAL,
            cash_basis_cents=0,
            subsidy_cents=20,
            idempotency_key="financial-intent",
            note="promotion",
        )
        assert created is True and wallet.available_points == 2
        _, replay_entry, replay_created = CompanyPointBillingService.credit(
            session,
            company_id=company_id,
            amount_points=2,
            source_kind=PointLotSourceKind.PROMOTIONAL,
            cash_basis_cents=0,
            subsidy_cents=20,
            idempotency_key="financial-intent",
            note="promotion",
        )
        assert replay_created is False and replay_entry.id == first_entry.id
        with pytest.raises(ConflictError, match="意图"):
            CompanyPointBillingService.credit(
                session,
                company_id=company_id,
                amount_points=2,
                source_kind=PointLotSourceKind.PURCHASED,
                cash_basis_cents=20,
                subsidy_cents=0,
                idempotency_key="financial-intent",
                note="purchase",
            )

    with app.state.session_factory() as session:
        wallet = session.get(CompanyPointWalletAccount, company_id)
        assert wallet is not None and wallet.available_points == 2
        assert session.scalar(
            select(func.count(CompanyPointLot.id)).where(
                CompanyPointLot.company_id == company_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id,
                CompanyPointLedgerEntry.kind == PointLedgerKind.CREDIT,
            )
        ) == 1


def test_expiring_promotional_lots_fail_closed_or_never_revive_after_release(app) -> None:
    """Expiry is safe whether the first slice implements it or rejects it.

    Accepting ``expires_at`` creates a stronger obligation: once an allocation
    reaches its expiry while reserved, failure release must expire it instead
    of turning it back into spendable points.  A vertical slice without that
    state transition must reject expiring credits up front.
    """

    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=0)
        company_id = company.id
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=0,
            idempotency_key="expiry-migration",
        )
        try:
            CompanyPointBillingService.credit(
                session,
                company_id=company_id,
                amount_points=4,
                source_kind=PointLotSourceKind.PROMOTIONAL,
                cash_basis_cents=0,
                subsidy_cents=40,
                idempotency_key="expiry-promo",
                note="expires while reserved",
                expires_at=utcnow() + timedelta(days=1),
            )
        except ConflictError:
            # Fail-closed is the approved first-slice fallback.
            return
        task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=4,
            key="expiry-release",
        )
        session.add(task)
        session.flush()
        task_id = task.id
        CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=task_id,
            amount_points=4,
            idempotency_key="expiry-reserve",
        )
        lot = session.scalar(
            select(CompanyPointLot).where(
                CompanyPointLot.company_id == company_id,
                CompanyPointLot.source_kind == PointLotSourceKind.PROMOTIONAL,
            )
        )
        assert lot is not None
        # Simulate time passing while the provider owns the reservation.
        lot.expires_at = utcnow() - timedelta(seconds=1)

    with app.state.session_factory.begin() as session:
        before = session.get(CompanyPointWalletAccount, company_id)
        assert before is not None and before.available_points == 0
        CompanyPointBillingService.release_failure(
            session,
            company_id=company_id,
            task_id=task_id,
            idempotency_key="expiry-release-ledger",
            failure_reason="expired while provider was processing",
        )

    with app.state.session_factory() as session:
        wallet = session.get(CompanyPointWalletAccount, company_id)
        lot = session.scalar(
            select(CompanyPointLot).where(
                CompanyPointLot.company_id == company_id,
                CompanyPointLot.source_kind == PointLotSourceKind.PROMOTIONAL,
            )
        )
        assert wallet is not None and wallet.available_points == 0
        assert wallet.reserved_points == 0
        assert lot is not None and lot.available_points == 0
        assert lot.reserved_points == 0
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id,
                CompanyPointLedgerEntry.kind == PointLedgerKind.EXPIRE,
                CompanyPointLedgerEntry.task_id == task_id,
            )
        ) == 1
        assert session.scalar(
            select(func.count(PersonalLedgerEntry.id)).where(
                PersonalLedgerEntry.idempotency_key == shared_key
            )
        ) == 1


def test_company_point_ledger_is_append_only(app) -> None:
    with app.state.session_factory.begin() as session:
        company, _, _ = _seed_company(session, available_cents=10)
        CompanyPointBillingService.migrate(
            session,
            company_id=company.id,
            expected_available_cents=10,
            idempotency_key="immutable-migration",
        )
        company_id = company.id

    with app.state.session_factory() as session:
        entry = session.scalar(
            select(CompanyPointLedgerEntry).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        )
        assert entry is not None
        entry.note = "tampered"
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()

    with app.state.session_factory() as session:
        entry = session.scalar(
            select(CompanyPointLedgerEntry).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        )
        assert entry is not None
        session.delete(entry)
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()


def test_promotional_point_settlement_is_not_reported_as_cash_revenue(app) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=0)
        company_id, model_id = company.id, model.id
        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=0,
            idempotency_key="report-migration",
        )
        CompanyPointBillingService.credit(
            session,
            company_id=company_id,
            amount_points=5,
            source_kind=PointLotSourceKind.PROMOTIONAL,
            cash_basis_cents=0,
            subsidy_cents=50,
            idempotency_key="report-promo",
            note="zero cash basis",
        )
        task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=5,
            key="report-points-task",
        )
        session.add(task)
        session.flush()
        CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=task.id,
            amount_points=5,
            idempotency_key="report-reserve",
        )
        CompanyPointBillingService.settle_success(
            session,
            company_id=company_id,
            task_id=task.id,
            actual_cost_points=5,
            idempotency_key="report-settle",
        )

    with app.state.session_factory() as session:
        report = AdminAnalyticsService.model_profitability(
            session,
            start=utcnow() - timedelta(days=1),
            end=utcnow() + timedelta(days=1),
        )
        row = next(item for item in report["items"] if item["model_id"] == model_id)
        assert row["succeeded_count"] == 1
        assert row["settled_revenue_cents"] == 0
        # A five-point promotional settlement has zero cash basis.  In
        # particular, it must never be presented as 5 * 10 = 50 cents revenue.
        assert row["settled_revenue_cents"] != 50
        assert row["revenue_reconciliation_status"] == "complete"
        assert row["point_subsidy_cents"] == 50
        # Attribution is now complete, but absent supplier cost still blocks profit.
        assert row["gross_profit_cents"] is None


def test_dunning_hold_blocks_contract_credit_but_preserves_noncredit_points_and_replay(
    app,
) -> None:
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_company(session, available_cents=0)
        CompanyPointBillingService.migrate(
            session,
            company_id=company.id,
            expected_available_cents=0,
            idempotency_key="hold-migration",
        )
        contract = CompanyBillingContractVersion(
            company_id=company.id,
            status=EnterpriseContractStatus.ACTIVE,
            contract_reference="hold-contract-v1",
            currency="CNY",
            timezone_name="Asia/Shanghai",
            cycle_day=1,
            payment_terms_days=30,
            credit_limit_points=20,
            receivable_per_point_cents=10,
            content_sha256="7" * 64,
            supersedes_version_id=None,
            effective_at=utcnow() - timedelta(days=30),
            expires_at=None,
            created_by_user_id=user.id,
        )
        session.add(contract)
        session.flush()
        session.add(
            CompanyBillingAccount(
                company_id=company.id,
                active_contract_version_id=contract.id,
                unbilled_receivable_cents=0,
                billing_hold=True,
                billing_hold_reason="delinquent_invoice",
                billing_hold_since=utcnow(),
                dunning_level=1,
            )
        )
        wallet = session.get(CompanyPointWalletAccount, company.id)
        assert wallet is not None
        wallet.available_points = 26
        session.add_all(
            [
                CompanyPointLot(
                    company_id=company.id,
                    source_kind=PointLotSourceKind.PROMOTIONAL,
                    original_points=6,
                    available_points=6,
                    reserved_points=0,
                    reversal_reserved_points=0,
                    settled_points=0,
                    reversed_points=0,
                    cash_basis_cents=0,
                    receivable_basis_cents=0,
                    subsidy_cents=60,
                    idempotency_key="hold-owned-lot",
                    expires_at=None,
                ),
                CompanyPointLot(
                    company_id=company.id,
                    source_kind=PointLotSourceKind.CONTRACT,
                    original_points=20,
                    available_points=20,
                    reserved_points=0,
                    reversal_reserved_points=0,
                    settled_points=0,
                    reversed_points=0,
                    cash_basis_cents=0,
                    receivable_basis_cents=200,
                    subsidy_cents=0,
                    idempotency_key="hold-contract-lot",
                    expires_at=None,
                    contract_version_id=contract.id,
                ),
            ]
        )
        blocked_task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=7,
            key="hold-blocked-task",
        )
        owned_task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=6,
            key="hold-owned-task",
        )
        session.add_all([blocked_task, owned_task])
        session.flush()
        company_id = company.id
        blocked_task_id = blocked_task.id
        owned_task_id = owned_task.id

    with app.state.session_factory.begin() as session:
        with pytest.raises(ConflictError, match="合同授信积分不能用于新任务"):
            CompanyPointBillingService.reserve(
                session,
                company_id=company_id,
                task_id=blocked_task_id,
                amount_points=7,
                idempotency_key="hold-blocked-reserve",
            )
        assert session.scalar(
            select(func.count(TaskPointLotAllocation.id)).where(
                TaskPointLotAllocation.task_id == blocked_task_id
            )
        ) == 0

    with app.state.session_factory.begin() as session:
        _, owned_entry = CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=owned_task_id,
            amount_points=6,
            idempotency_key="hold-owned-reserve",
        )
        allocations = list(
            session.scalars(
                select(TaskPointLotAllocation).where(
                    TaskPointLotAllocation.task_id == owned_task_id
                )
            ).all()
        )
        assert len(allocations) == 1
        lot = session.get(CompanyPointLot, allocations[0].lot_id)
        assert lot is not None and lot.source_kind == PointLotSourceKind.PROMOTIONAL
        owned_entry_id = owned_entry.id

    with app.state.session_factory.begin() as session:
        # A later dunning hold must not make an already committed reserve look
        # like a failed request when its idempotency key is replayed.
        _, replay = CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=owned_task_id,
            amount_points=6,
            idempotency_key="hold-owned-reserve",
        )
        assert replay.id == owned_entry_id
        contract_lot = session.scalar(
            select(CompanyPointLot).where(
                CompanyPointLot.company_id == company_id,
                CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
            )
        )
        assert contract_lot is not None
        assert (contract_lot.available_points, contract_lot.reserved_points) == (20, 0)
