from __future__ import annotations

import os
from threading import Barrier, Lock, Thread
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from platform_api.database import Base
from platform_api.models import (
    BillingUnit,
    Company,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    GenerationTask,
    ModelDefinition,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalTaskPointLotAllocation,
    PersonalWalletAccount,
    PersonalWorkspace,
    PointLotSourceKind,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    UserAccountType,
)
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.errors import InsufficientBalanceError
from platform_api.services.personal_billing import (
    InsufficientPersonalPointsError,
    PersonalWalletService,
)


DATABASE_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


@pytest.fixture
def postgres_points_factory():
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    schema_name = f"points_v2_{uuid.uuid4().hex}"
    assert schema_name.startswith("points_v2_")
    administration_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')

    engine = create_engine(
        DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema_name}"},
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()


@pytest.mark.parametrize("preload_projection", (False, True))
def test_postgres_company_point_reservations_cannot_overdraw_shared_wallet(
    postgres_points_factory, preload_projection,
) -> None:
    factory = postgres_points_factory
    with factory.begin() as session:
        suffix = uuid.uuid4().hex
        company = Company(name="Concurrent points", billing_version=2)
        first_user = User(
            email=f"points-concurrent-one-{suffix}@example.test",
            display_name="One",
            account_type=UserAccountType.COMPANY,
        )
        second_user = User(
            email=f"points-concurrent-two-{suffix}@example.test",
            display_name="Two",
            account_type=UserAccountType.COMPANY,
        )
        model = ModelDefinition(
            slug=f"points-concurrent-{suffix}",
            display_name="Concurrent points",
            provider_key="postgres-test",
            billing_mode="per_item",
        )
        session.add_all([company, first_user, second_user, model])
        session.flush()
        session.add(
            CompanyPointWalletAccount(
                company_id=company.id,
                available_points=100,
                reserved_points=0,
                migration_idempotency_key=f"migration-{suffix}",
                migrated_from_available_cents=1_000,
                migration_remainder_cents=0,
                migration_rounding_grant_points=0,
            )
        )
        session.add(
            CompanyPointLot(
                company_id=company.id,
                source_kind=PointLotSourceKind.LEGACY,
                original_points=100,
                available_points=100,
                reserved_points=0,
                settled_points=0,
                cash_basis_cents=1_000,
                subsidy_cents=0,
                idempotency_key=f"lot-{suffix}",
            )
        )
        task_ids: list[str] = []
        for index, user in enumerate((first_user, second_user), start=1):
            task = GenerationTask(
                company_id=company.id,
                personal_workspace_id=None,
                user_id=user.id,
                model_id=model.id,
                idempotency_key=f"concurrent-task-{index}-{suffix}",
                request_fingerprint=(str(index) * 64),
                status=TaskStatus.DRAFT,
                request_payload={"prompt": f"task {index}"},
                billing_unit=BillingUnit.POINT,
                billing_version=2,
                quote_cents=None,
                quote_points=70,
                pricing_snapshot={
                    "schema_version": 2,
                    "billing_unit": BillingUnit.POINT.value,
                    "billing_version": 2,
                    "quote_points": 70,
                },
                capability_snapshot={},
                reserved_cents=0,
                reserved_points=0,
                actual_cost_cents=None,
                actual_cost_points=None,
            )
            session.add(task)
            session.flush()
            task_ids.append(task.id)
        company_id = company.id

    barrier = Barrier(2)
    result_lock = Lock()
    successes: list[str] = []
    insufficient: list[str] = []
    unexpected: list[BaseException] = []

    def reserve(task_id: str, index: int) -> None:
        try:
            with factory.begin() as session:
                if preload_projection:
                    # Both sessions retain the same old projection before
                    # either owns the wallet lock. FOR UPDATE alone does not
                    # refresh an already loaded SQLAlchemy identity.
                    preloaded_wallet = session.get(CompanyPointWalletAccount, company_id)
                    preloaded_lots = list(session.scalars(
                        select(CompanyPointLot).where(CompanyPointLot.company_id == company_id)
                    ))
                    assert preloaded_wallet.available_points == 100
                    assert sum(lot.available_points for lot in preloaded_lots) == 100
                barrier.wait(timeout=10)
                CompanyPointBillingService.reserve(
                    session,
                    company_id=company_id,
                    task_id=task_id,
                    amount_points=70,
                    idempotency_key=f"concurrent-reserve-{index}",
                )
            with result_lock:
                successes.append(task_id)
        except InsufficientBalanceError:
            with result_lock:
                insufficient.append(task_id)
        except BaseException as exc:  # pragma: no cover - diagnostic evidence
            with result_lock:
                unexpected.append(exc)

    threads = [
        Thread(target=reserve, args=(task_id, index), daemon=True)
        for index, task_id in enumerate(task_ids, start=1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert unexpected == []
    assert len(successes) == 1
    assert len(insufficient) == 1

    with factory() as session:
        wallet = session.get(CompanyPointWalletAccount, company_id)
        assert wallet is not None
        assert (wallet.available_points, wallet.reserved_points) == (30, 70)
        lot = session.scalar(
            select(CompanyPointLot).where(CompanyPointLot.company_id == company_id)
        )
        assert lot is not None
        assert (lot.available_points, lot.reserved_points, lot.settled_points) == (
            30,
            70,
            0,
        )
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.company_id == company_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(TaskPointLotAllocation.id)).where(
                TaskPointLotAllocation.company_id == company_id
            )
        ) == 1
        task_statuses = dict(
            session.execute(
                select(GenerationTask.id, GenerationTask.status).where(
                    GenerationTask.id.in_(task_ids)
                )
            ).all()
        )
        assert sorted(task_statuses.values(), key=lambda status: status.value) == [
            TaskStatus.DRAFT,
            TaskStatus.QUEUED,
        ]


def test_postgres_personal_reservation_refreshes_preloaded_wallet_and_lots(
    postgres_points_factory,
) -> None:
    factory = postgres_points_factory
    suffix = uuid.uuid4().hex
    with factory.begin() as session:
        user = User(
            email=f"personal-lock-{suffix}@example.test", display_name="Personal lock",
            account_type=UserAccountType.PERSONAL,
        )
        model = ModelDefinition(
            slug=f"personal-lock-{suffix}", display_name="Personal lock",
            provider_key="postgres-test", billing_mode="per_item",
        )
        session.add_all([user, model])
        session.flush()
        workspace = PersonalWorkspace(user_id=user.id)
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id
        session.add(PersonalWalletAccount(
            workspace_id=workspace_id, available_points=100, reserved_points=0,
        ))
        session.add(PersonalPointLot(
            workspace_id=workspace_id, source_kind=PointLotSourceKind.LEGACY,
            original_points=100, available_points=100, reserved_points=0,
            settled_points=0, cash_basis_cents=0, subsidy_cents=1000, refundable=False,
            idempotency_key=f"lot-{suffix}",
        ))
        task_ids = []
        for index in (1, 2):
            task = GenerationTask(
                personal_workspace_id=workspace_id, company_id=None,
                user_id=user.id, model_id=model.id,
                idempotency_key=f"personal-lock-{index}-{suffix}",
                request_fingerprint=str(index) * 64, status=TaskStatus.DRAFT,
                request_payload={"prompt": "locking regression"},
                billing_unit=BillingUnit.POINT, billing_version=2,
                quote_points=70, quote_cents=None, pricing_snapshot={},
                capability_snapshot={}, reserved_cents=0, reserved_points=0,
            )
            session.add(task)
            session.flush()
            task_ids.append(task.id)

    barrier = Barrier(2)
    result_lock = Lock()
    outcomes = []
    failures = []

    def reserve(task_id):
        try:
            with factory.begin() as session:
                preloaded_wallet = session.get(PersonalWalletAccount, workspace_id)
                preloaded_lots = list(session.scalars(select(PersonalPointLot).where(
                    PersonalPointLot.workspace_id == workspace_id
                )))
                assert preloaded_wallet.available_points == 100
                assert sum(lot.available_points for lot in preloaded_lots) == 100
                barrier.wait(timeout=10)
                PersonalWalletService.reserve(
                    session, workspace_id=workspace_id, task_id=task_id,
                    amount_points=70, idempotency_key=f"reserve-{task_id}",
                )
            outcome = "reserved"
        except InsufficientPersonalPointsError:
            outcome = "insufficient"
        except BaseException as exc:  # pragma: no cover - concurrent diagnostic.
            with result_lock:
                failures.append(exc)
            return
        with result_lock:
            outcomes.append(outcome)

    threads = [Thread(target=reserve, args=(task_id,), daemon=True) for task_id in task_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert sorted(outcomes) == ["insufficient", "reserved"]
    with factory() as session:
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert (wallet.available_points, wallet.reserved_points) == (30, 70)
        assert session.scalar(select(func.sum(PersonalPointLot.available_points)).where(
            PersonalPointLot.workspace_id == workspace_id
        )) == 30
        assert session.scalar(select(func.sum(PersonalTaskPointLotAllocation.reserved_points)).where(
            PersonalTaskPointLotAllocation.workspace_id == workspace_id
        )) == 70
        assert session.scalar(select(func.count(PersonalLedgerEntry.id)).where(
            PersonalLedgerEntry.workspace_id == workspace_id
        )) == 1
