"""Explicit, app-scoped unit isolation for pre-commercial cents fixtures.

These tests exercise ledger arithmetic, capabilities or relay orchestration.
Their synthetic route/cost documents are NOT commercial publication evidence.
Never import this module from product code or install it as an autouse fixture.
The real policy still runs for every session except a specifically marked
in-memory SQLite test app, and any commercial plan in that app is an error.
"""
from __future__ import annotations

from sqlalchemy import select

from platform_api.models import ModelCommercialReleasePlan
from platform_api.services.commercial_pricing import CommercialPricingPolicy


_FLAG = "legacy_unit_integration_commercial_gate_isolated"
_real_require_price = CommercialPricingPolicy.require_price
_real_task_evidence = CommercialPricingPolicy.task_needs_live_evidence
_real_distribution_blockers = CommercialPricingPolicy.distribution_blockers
_installed = False


def _isolated(session):
    if not session.info.get(_FLAG):
        return False
    assert session.get_bind().dialect.name == "sqlite"
    assert session.scalar(select(ModelCommercialReleasePlan.id).limit(1)) is None, (
        "Commercial plan/price/task acceptance must not use legacy dependency isolation"
    )
    return True


def isolate_legacy_commercial_gate(app):
    """Install only for an explicitly selected old unit-integration app."""
    global _installed
    assert app.state.engine.dialect.name == "sqlite"
    assert app.state.engine.url.database in {None, "", ":memory:"}
    with app.state.session_factory() as session:
        assert session.scalar(select(ModelCommercialReleasePlan.id).limit(1)) is None
    info = dict(app.state.session_factory.kw.get("info", {}))
    info[_FLAG] = True
    app.state.session_factory.configure(info=info)
    app.state.legacy_commercial_gate_isolated = True
    if _installed:
        return

    def require_price(cls, session, **kwargs):
        if _isolated(session):
            return None
        return _real_require_price(session, **kwargs)

    def task_needs_live_evidence(session, **kwargs):
        if _isolated(session):
            return False
        return _real_task_evidence(session, **kwargs)

    def distribution_blockers(cls, session, **kwargs):
        if _isolated(session):
            return ()
        return _real_distribution_blockers(session, **kwargs)

    CommercialPricingPolicy.require_price = classmethod(require_price)
    CommercialPricingPolicy.task_needs_live_evidence = staticmethod(task_needs_live_evidence)
    CommercialPricingPolicy.distribution_blockers = classmethod(distribution_blockers)
    _installed = True
