from __future__ import annotations

import pytest

from platform_api.models import (
    CompanyPointWalletAccount,
    GenerationTask,
    PersonalWalletAccount,
    TaskStatus,
    WalletAccount,
)
from platform_api.services.billing import WalletService
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.errors import ConflictError
from platform_api.services.personal_billing import PersonalWalletService
from platform_api.services.reservation_recovery import ReservationRecoveryService

from .test_billing_recovery_safety import seed_reserved_submission

SCOPES = ["company_cents", "company_points", "personal_points"]


def _wallet(session, scope, identity):
    _, _, company_id, workspace_id = identity
    if scope == "company_cents":
        return session.get(WalletAccount, company_id), "cents"
    if scope == "company_points":
        return session.get(CompanyPointWalletAccount, company_id), "points"
    return session.get(PersonalWalletAccount, workspace_id), "points"


def _balance(wallet, unit):
    return getattr(wallet, f"available_{unit}"), getattr(wallet, f"reserved_{unit}")


def _refuse_without_recovery(factory, identity, scope):
    """Every ordinary path must refuse an already-terminal task.

    This is exactly why a reservation can stay held forever: status and ledger
    are two separate writes, and nothing but the recovery sweep can close it.
    """
    task_id, _, company_id, workspace_id = identity
    with pytest.raises(ConflictError):
        with factory.begin() as session:
            if scope == "personal_points":
                PersonalWalletService.settle_success(
                    session,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    actual_cost_points=40,
                    idempotency_key="late-callback",
                )
            elif scope == "company_points":
                CompanyPointBillingService.settle_success(
                    session,
                    company_id=company_id,
                    task_id=task_id,
                    actual_cost_points=40,
                    idempotency_key="late-callback",
                )
            else:
                WalletService.settle_success(
                    session,
                    company_id=company_id,
                    task_id=task_id,
                    actual_cost_cents=40,
                    idempotency_key="late-callback",
                )


@pytest.mark.parametrize("scope", SCOPES)
def test_succeeded_task_without_settlement_is_recovered(app, scope):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, _, _, _ = identity

    with factory.begin() as session:
        session.get(GenerationTask, task_id).status = TaskStatus.SUCCEEDED

    _refuse_without_recovery(factory, identity, scope)

    with factory.begin() as session:
        result = ReservationRecoveryService.run_once(session)
    assert result["processed"] is True
    assert result["scanned"] == 1
    assert result["failed"] == []

    with factory() as session:
        task = session.get(GenerationTask, task_id)
        wallet, unit = _wallet(session, scope, identity)
        assert task.status == TaskStatus.SUCCEEDED
        # A succeeded task is charged, not refunded: 40 stays consumed.
        assert _balance(wallet, unit) == (60, 0)


@pytest.mark.parametrize("scope", SCOPES)
def test_failed_task_without_release_is_recovered(app, scope):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, _, _, _ = identity

    with factory.begin() as session:
        session.get(GenerationTask, task_id).status = TaskStatus.FAILED

    with factory.begin() as session:
        result = ReservationRecoveryService.run_once(session)
    assert result["failed"] == []

    with factory() as session:
        task = session.get(GenerationTask, task_id)
        wallet, unit = _wallet(session, scope, identity)
        assert task.status == TaskStatus.FAILED
        # A failed task gets its reservation back.
        assert _balance(wallet, unit) == (100, 0)


@pytest.mark.parametrize("scope", SCOPES)
def test_recovery_is_idempotent(app, scope):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, _, _, _ = identity

    with factory.begin() as session:
        session.get(GenerationTask, task_id).status = TaskStatus.SUCCEEDED
    with factory.begin() as session:
        ReservationRecoveryService.run_once(session)

    for _ in range(2):
        with factory.begin() as session:
            assert ReservationRecoveryService.run_once(session)["scanned"] == 0

    with factory() as session:
        wallet, unit = _wallet(session, scope, identity)
        assert _balance(wallet, unit) == (60, 0)


@pytest.mark.parametrize("scope", SCOPES)
def test_recovery_ignores_tasks_that_are_not_terminal(app, scope):
    factory = app.state.session_factory
    seed_reserved_submission(factory, scope)

    with factory.begin() as session:
        result = ReservationRecoveryService.run_once(session)
    assert result == {"processed": False, "scanned": 0, "recovered": [], "failed": []}


@pytest.mark.parametrize("scope", SCOPES)
def test_default_paths_still_reject_terminal_tasks(app, scope):
    """The recovery switch must not loosen the ordinary billing contract."""
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, _, company_id, workspace_id = identity

    with factory.begin() as session:
        session.get(GenerationTask, task_id).status = TaskStatus.SUCCEEDED

    with factory.begin() as session:
        if scope == "personal_points":
            PersonalWalletService.settle_success(
                session,
                workspace_id=workspace_id,
                task_id=task_id,
                actual_cost_points=40,
                idempotency_key="recovery",
                allow_terminal=True,
            )
        elif scope == "company_points":
            CompanyPointBillingService.settle_success(
                session,
                company_id=company_id,
                task_id=task_id,
                actual_cost_points=40,
                idempotency_key="recovery",
                allow_terminal=True,
            )
        else:
            WalletService.settle_success(
                session,
                company_id=company_id,
                task_id=task_id,
                actual_cost_cents=40,
                idempotency_key="recovery",
                allow_terminal=True,
            )

    # Without the switch the late ordinary callback is still refused, and the
    # already-settled task is no longer picked up by the sweep.
    _refuse_without_recovery(factory, identity, scope)
    with factory.begin() as session:
        assert ReservationRecoveryService.run_once(session)["scanned"] == 0
