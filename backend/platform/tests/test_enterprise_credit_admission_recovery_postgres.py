"""Execute the money-risk regressions against real PostgreSQL transactions."""
from types import SimpleNamespace

import pytest

from . import test_enterprise_credit_admission_recovery as recovery
from .test_enterprise_billing_postgres import enterprise_postgres_factory


@pytest.fixture
def credit_recovery_postgres_app(enterprise_postgres_factory):
    return SimpleNamespace(state=SimpleNamespace(session_factory=enterprise_postgres_factory))


@pytest.mark.parametrize("condition", ["valid", "cycle_ended", "terminated", "no_cycle"])
def test_postgres_invalid_credit_admission(credit_recovery_postgres_app, monkeypatch, condition):
    recovery.test_new_contract_reserve_requires_current_contract_and_open_cycle(
        credit_recovery_postgres_app, monkeypatch, condition,
        recovery.END if condition in {"valid", "cycle_ended"} else recovery.START,
    )


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
def test_postgres_inflight_credit_survives_expiry(credit_recovery_postgres_app, monkeypatch, outcome):
    recovery.test_expiry_and_hold_preserve_inflight_reserve_replay_and_terminal_settlement(
        credit_recovery_postgres_app, monkeypatch, outcome,
    )


@pytest.mark.parametrize("scenario", [
    "proven_overdue", "late_refund", "late_win", "multiple_invoices", "owned_points",
    "overdue_admission",
])
def test_postgres_payment_risk_recovery(credit_recovery_postgres_app, monkeypatch, scenario):
    check = {
        "proven_overdue": recovery.test_proven_overdue_cannot_be_undone_by_late_capture_or_a_backward_clock,
        "late_refund": recovery.test_late_refund_reopens_overdue_receivable_at_processing_time,
        "late_win": lambda app, patch: recovery.test_late_dispute_win_preserves_remaining_overdue_then_full_payment_clears(app, patch, 40),
        "multiple_invoices": recovery.test_full_payment_of_one_invoice_keeps_hold_for_other_due_invoice_without_worker,
        "owned_points": recovery.test_expired_contract_does_not_confiscate_purchased_points,
        "overdue_admission": recovery.test_overdue_invoice_blocks_new_credit_without_waiting_for_dunning_worker,
    }[scenario]
    check(credit_recovery_postgres_app, monkeypatch)


@pytest.mark.parametrize("closed_period", [False, True])
def test_postgres_unbilled_receivable_exceptions(credit_recovery_postgres_app, closed_period):
    check = (
        recovery.test_open_cycle_can_collect_late_settlement_but_closed_invoice_is_not_rewritten
        if closed_period else recovery.test_late_settlement_after_final_cycle_is_explicit_unbilled_exception
    )
    check(credit_recovery_postgres_app)
