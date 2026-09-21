"""Add versioned company points billing without relabelling legacy cents.

Revision ID: 0046_company_points_billing_v2
Revises: 0045_system_audit_actor
"""

from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v10 as policy_v10
from platform_api import database_privileges_v11 as policy_v11
from platform_api.database_privileges_behavior_v10 import (
    collect_platform_database_evidence as collect_v10_database_evidence,
    validate_platform_database_acl_evidence as validate_v10_database_acl_evidence,
)
from platform_api.database_privileges_behavior_v11 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v11,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0046_company_points_billing_v2"
down_revision: str | None = "0045_system_audit_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_POSTGRES_GUARD_FUNCTIONS = (
    "reject_company_point_ledger_mutation",
    "guard_company_point_price_version_scope",
    "guard_grant_point_price_version_scope",
    "guard_company_point_ledger_task_scope",
    "guard_task_timeout_point_ledger_scope",
    "guard_company_point_lot_mutation",
    "guard_task_point_allocation",
    "guard_legacy_company_billing_write",
    "guard_task_billing_contract",
    "guard_company_billing_downgrade",
)


_TASK_SCOPE_QUOTE_V2 = (
    "(company_id IS NOT NULL AND personal_workspace_id IS NULL AND ("
    "(billing_unit = 'CNY_CENT' AND billing_version = 1 "
    "AND quote_cents > 0 AND quote_points IS NULL "
    "AND reserved_points = 0 AND actual_cost_points IS NULL) OR "
    "(billing_unit = 'POINT' AND billing_version = 2 "
    "AND quote_cents IS NULL AND quote_points > 0 "
    "AND reserved_cents = 0 AND actual_cost_cents IS NULL))) OR "
    "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
    "AND billing_unit = 'POINT' AND billing_version = 2 "
    "AND quote_cents IS NULL AND quote_points > 0 "
    "AND reserved_cents = 0 AND actual_cost_cents IS NULL)"
)

_GRANT_PRICE_V2 = (
    "(price_per_second_cents IS NULL AND price_per_item_cents IS NULL "
    "AND price_per_second_points IS NULL AND price_per_item_points IS NULL) OR "
    "((price_per_second_cents IS NOT NULL AND price_per_item_cents IS NULL) "
    "OR (price_per_second_cents IS NULL AND price_per_item_cents IS NOT NULL)) "
    "AND price_per_second_points IS NULL AND price_per_item_points IS NULL "
    "OR ((price_per_second_points IS NOT NULL AND price_per_item_points IS NULL) "
    "OR (price_per_second_points IS NULL AND price_per_item_points IS NOT NULL)) "
    "AND price_per_second_cents IS NULL AND price_per_item_cents IS NULL"
)


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _apply_company_points_acl() -> None:
    """Grant only the frozen v11 privileges on the five new point tables."""

    runtime_roles = tuple(
        role
        for process, role in policy_v11.DATABASE_ROLE_BY_PROCESS.items()
        if process != "migration"
    )
    for table_name in sorted(policy_v11.POINT_TABLES):
        quoted_table = _quote_identifier(table_name)
        _execute(
            "REVOKE ALL PRIVILEGES ON TABLE public."
            f"{quoted_table} FROM PUBLIC"
        )
        for database_role in runtime_roles:
            _execute(
                "REVOKE ALL PRIVILEGES ON TABLE public."
                f"{quoted_table} FROM {_quote_identifier(database_role)}"
            )

    for process_role, privileges_by_table in (
        policy_v11.PRIVILEGES_BY_PROCESS.items()
    ):
        quoted_role = _quote_identifier(
            policy_v11.DATABASE_ROLE_BY_PROCESS[process_role]
        )
        for table_name in sorted(policy_v11.POINT_TABLES):
            privileges = privileges_by_table.get(table_name)
            if not privileges:
                continue
            privilege_list = ", ".join(sorted(privileges))
            _execute(
                f"GRANT {privilege_list} ON TABLE public."
                f"{_quote_identifier(table_name)} TO {quoted_role}"
            )

    # Trigger execution does not require callers to hold EXECUTE on the
    # underlying functions. Keep the public routine surface empty even in a
    # rehearsal database whose default privileges were not pre-hardened.
    for function_name in _POSTGRES_GUARD_FUNCTIONS:
        quoted_function = _quote_identifier(function_name)
        _execute(
            "REVOKE ALL PRIVILEGES ON FUNCTION public."
            f"{quoted_function}() FROM PUBLIC"
        )
        for database_role in runtime_roles:
            _execute(
                "REVOKE ALL PRIVILEGES ON FUNCTION public."
                f"{quoted_function}() FROM {_quote_identifier(database_role)}"
            )


def _create_sqlite_guards() -> None:
    statements = (
        """
        CREATE TRIGGER trg_company_point_ledger_no_update
        BEFORE UPDATE ON company_point_ledger_entries
        BEGIN SELECT RAISE(ABORT, 'company point ledger is immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_point_ledger_no_delete
        BEFORE DELETE ON company_point_ledger_entries
        BEGIN SELECT RAISE(ABORT, 'company point ledger is immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_point_price_version_no_update
        BEFORE UPDATE ON company_point_price_versions
        BEGIN SELECT RAISE(ABORT, 'company point price versions are immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_point_price_version_no_delete
        BEFORE DELETE ON company_point_price_versions
        BEGIN SELECT RAISE(ABORT, 'company point price versions are immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_point_price_version_scope_insert
        BEFORE INSERT ON company_point_price_versions
        WHEN NOT EXISTS (
          SELECT 1 FROM company_model_grants g
          JOIN model_definitions m ON m.id = g.model_id
          WHERE g.id = NEW.grant_id
            AND g.company_id = NEW.company_id
            AND g.model_id = NEW.model_id
            AND m.billing_mode = NEW.billing_mode
        ) OR (NEW.supersedes_version_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM company_point_price_versions prior
          WHERE prior.id = NEW.supersedes_version_id
            AND prior.company_id = NEW.company_id
            AND prior.grant_id = NEW.grant_id
            AND prior.model_id = NEW.model_id
        ))
        BEGIN SELECT RAISE(ABORT, 'point price version scope mismatch'); END
        """,
        """
        CREATE TRIGGER trg_grant_point_price_version_scope_insert
        BEFORE INSERT ON company_model_grants
        WHEN (NEW.point_price_candidate_version_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM company_point_price_versions v
          WHERE v.id = NEW.point_price_candidate_version_id
            AND v.company_id = NEW.company_id AND v.grant_id = NEW.id
            AND v.model_id = NEW.model_id AND v.status = 'CANDIDATE'
        )) OR (NEW.point_price_active_version_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM company_point_price_versions v
          WHERE v.id = NEW.point_price_active_version_id
            AND v.company_id = NEW.company_id AND v.grant_id = NEW.id
            AND v.model_id = NEW.model_id AND v.status = 'ACTIVE'
        ))
        BEGIN SELECT RAISE(ABORT, 'grant point price version scope mismatch'); END
        """,
        """
        CREATE TRIGGER trg_grant_point_price_version_scope_update
        BEFORE UPDATE OF company_id, model_id, point_price_candidate_version_id,
          point_price_active_version_id
        ON company_model_grants
        WHEN (NEW.point_price_candidate_version_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM company_point_price_versions v
          WHERE v.id = NEW.point_price_candidate_version_id
            AND v.company_id = NEW.company_id AND v.grant_id = NEW.id
            AND v.model_id = NEW.model_id AND v.status = 'CANDIDATE'
        )) OR (NEW.point_price_active_version_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM company_point_price_versions v
          WHERE v.id = NEW.point_price_active_version_id
            AND v.company_id = NEW.company_id AND v.grant_id = NEW.id
            AND v.model_id = NEW.model_id AND v.status = 'ACTIVE'
        ))
        BEGIN SELECT RAISE(ABORT, 'grant point price version scope mismatch'); END
        """,
        """
        CREATE TRIGGER trg_company_point_ledger_task_scope_insert
        BEFORE INSERT ON company_point_ledger_entries
        WHEN (NEW.task_id IS NULL AND NEW.kind NOT IN ('MIGRATION', 'CREDIT'))
          OR (NEW.task_id IS NOT NULL AND NEW.kind NOT IN ('RESERVE', 'SETTLE', 'RELEASE'))
          OR (NEW.task_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM generation_tasks t
            WHERE t.id = NEW.task_id AND t.company_id = NEW.company_id
              AND t.personal_workspace_id IS NULL
              AND t.billing_unit = 'POINT' AND t.billing_version = 2
          ))
        BEGIN SELECT RAISE(ABORT, 'company point ledger task scope mismatch'); END
        """,
        """
        CREATE TRIGGER trg_task_timeout_point_ledger_scope_insert
        BEFORE INSERT ON task_timeout_events
        WHEN NEW.company_point_ledger_entry_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM company_point_ledger_entries e
          JOIN generation_tasks t ON t.id = NEW.task_id
          WHERE e.id = NEW.company_point_ledger_entry_id
            AND e.company_id = NEW.company_id
            AND e.task_id = NEW.task_id
            AND ((NEW.released_points > 0 AND e.kind = 'RELEASE'
                  AND e.amount_points = NEW.released_points)
              OR (NEW.released_points = 0 AND e.kind = 'SETTLE'))
            AND t.company_id = NEW.company_id
            AND t.billing_unit = 'POINT'
            AND t.billing_version = 2
        )
        BEGIN SELECT RAISE(ABORT, 'task timeout point ledger scope mismatch'); END
        """,
        """
        CREATE TRIGGER trg_task_timeout_event_no_update
        BEFORE UPDATE ON task_timeout_events
        BEGIN SELECT RAISE(ABORT, 'task timeout events are immutable'); END
        """,
        """
        CREATE TRIGGER trg_task_timeout_event_no_delete
        BEFORE DELETE ON task_timeout_events
        BEGIN SELECT RAISE(ABORT, 'task timeout events are immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_point_lot_identity_immutable
        BEFORE UPDATE OF company_id, source_kind, original_points,
          cash_basis_cents, subsidy_cents, idempotency_key, expires_at
        ON company_point_lots
        BEGIN SELECT RAISE(ABORT, 'company point lot identity is immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_point_lot_settled_monotonic
        BEFORE UPDATE ON company_point_lots
        WHEN NEW.settled_points < OLD.settled_points
        BEGIN SELECT RAISE(ABORT, 'company point settled total cannot decrease'); END
        """,
        """
        CREATE TRIGGER trg_company_point_lot_no_delete
        BEFORE DELETE ON company_point_lots
        BEGIN SELECT RAISE(ABORT, 'company point lots are durable'); END
        """,
        """
        CREATE TRIGGER trg_task_point_allocation_scope_insert
        BEFORE INSERT ON task_point_lot_allocations
        WHEN NOT EXISTS (
          SELECT 1 FROM generation_tasks t
          JOIN company_point_lots l ON l.id = NEW.lot_id
          WHERE t.id = NEW.task_id
            AND t.company_id = NEW.company_id
            AND t.personal_workspace_id IS NULL
            AND t.billing_unit = 'POINT'
            AND t.billing_version = 2
            AND l.company_id = NEW.company_id
        )
        BEGIN SELECT RAISE(ABORT, 'point allocation scope mismatch'); END
        """,
        """
        CREATE TRIGGER trg_task_point_allocation_scope_update
        BEFORE UPDATE ON task_point_lot_allocations
        WHEN NEW.company_id <> OLD.company_id
          OR NEW.task_id <> OLD.task_id
          OR NEW.lot_id <> OLD.lot_id
          OR NEW.allocated_points <> OLD.allocated_points
          OR NEW.settled_points < OLD.settled_points
          OR NEW.released_points < OLD.released_points
        BEGIN SELECT RAISE(ABORT, 'point allocation identity is immutable'); END
        """,
        """
        CREATE TRIGGER trg_task_point_allocation_no_delete
        BEFORE DELETE ON task_point_lot_allocations
        BEGIN SELECT RAISE(ABORT, 'point allocations are durable'); END
        """,
        """
        CREATE TRIGGER trg_legacy_ledger_block_v2_insert
        BEFORE INSERT ON ledger_entries
        WHEN EXISTS (
          SELECT 1 FROM companies c
          WHERE c.id = NEW.company_id AND c.billing_version = 2
        )
        BEGIN SELECT RAISE(ABORT, 'legacy cents ledger is read-only after migration'); END
        """,
        """
        CREATE TRIGGER trg_legacy_wallet_block_v2_update
        BEFORE UPDATE ON wallet_accounts
        WHEN EXISTS (
          SELECT 1 FROM companies c
          WHERE c.id = NEW.company_id AND c.billing_version = 2
        )
        BEGIN SELECT RAISE(ABORT, 'legacy cents wallet is read-only after migration'); END
        """,
        """
        CREATE TRIGGER trg_legacy_wallet_block_v2_delete
        BEFORE DELETE ON wallet_accounts
        WHEN EXISTS (
          SELECT 1 FROM companies c
          WHERE c.id = OLD.company_id AND c.billing_version = 2
        )
        BEGIN SELECT RAISE(ABORT, 'legacy cents wallet is read-only after migration'); END
        """,
        """
        CREATE TRIGGER trg_task_billing_scope_insert
        BEFORE INSERT ON generation_tasks
        WHEN NEW.company_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM companies c WHERE c.id = NEW.company_id AND (
            (c.billing_version = 1 AND NEW.billing_unit = 'CNY_CENT'
              AND NEW.billing_version = 1)
            OR (c.billing_version = 2 AND NEW.billing_unit = 'POINT'
              AND NEW.billing_version = 2)
          )
        )
        BEGIN SELECT RAISE(ABORT, 'task billing contract mismatches company'); END
        """,
        """
        CREATE TRIGGER trg_task_billing_contract_immutable
        BEFORE UPDATE OF company_id, personal_workspace_id, billing_unit,
          billing_version, quote_cents, quote_points
        ON generation_tasks
        BEGIN SELECT RAISE(ABORT, 'task billing contract is immutable'); END
        """,
        """
        CREATE TRIGGER trg_company_billing_no_downgrade
        BEFORE UPDATE OF billing_version ON companies
        WHEN OLD.billing_version = 2 AND NEW.billing_version <> 2
        BEGIN SELECT RAISE(ABORT, 'company billing version cannot downgrade'); END
        """,
    )
    for statement in statements:
        _execute(statement)


def _drop_sqlite_guards() -> None:
    for name in (
        "trg_company_point_ledger_no_update",
        "trg_company_point_ledger_no_delete",
        "trg_company_point_price_version_no_update",
        "trg_company_point_price_version_no_delete",
        "trg_company_point_price_version_scope_insert",
        "trg_grant_point_price_version_scope_insert",
        "trg_grant_point_price_version_scope_update",
        "trg_company_point_ledger_task_scope_insert",
        "trg_task_timeout_point_ledger_scope_insert",
        "trg_task_timeout_event_no_update",
        "trg_task_timeout_event_no_delete",
        "trg_company_point_lot_identity_immutable",
        "trg_company_point_lot_settled_monotonic",
        "trg_company_point_lot_no_delete",
        "trg_task_point_allocation_scope_insert",
        "trg_task_point_allocation_scope_update",
        "trg_task_point_allocation_no_delete",
        "trg_legacy_ledger_block_v2_insert",
        "trg_legacy_wallet_block_v2_update",
        "trg_legacy_wallet_block_v2_delete",
        "trg_task_billing_scope_insert",
        "trg_task_billing_contract_immutable",
        "trg_company_billing_no_downgrade",
    ):
        _execute(f"DROP TRIGGER IF EXISTS {name}")


def _create_postgres_guards() -> None:
    _execute(
        """
        CREATE FUNCTION reject_company_point_ledger_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'company point ledger is immutable'; END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_company_point_ledger_immutable "
        "BEFORE UPDATE OR DELETE ON company_point_ledger_entries "
        "FOR EACH ROW EXECUTE FUNCTION reject_company_point_ledger_mutation()"
    )
    _execute(
        "CREATE TRIGGER trg_company_point_price_version_immutable "
        "BEFORE UPDATE OR DELETE ON company_point_price_versions "
        "FOR EACH ROW EXECUTE FUNCTION reject_company_point_ledger_mutation()"
    )
    _execute(
        """
        CREATE FUNCTION guard_company_point_price_version_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM company_model_grants g
            JOIN model_definitions m ON m.id = g.model_id
            WHERE g.id = NEW.grant_id
              AND g.company_id = NEW.company_id
              AND g.model_id = NEW.model_id
              AND m.billing_mode = NEW.billing_mode
          ) THEN
            RAISE EXCEPTION 'point price version scope mismatch';
          END IF;
          IF NEW.supersedes_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM company_point_price_versions prior
            WHERE prior.id = NEW.supersedes_version_id
              AND prior.company_id = NEW.company_id
              AND prior.grant_id = NEW.grant_id
              AND prior.model_id = NEW.model_id
          ) THEN
            RAISE EXCEPTION 'superseded point price version scope mismatch';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_company_point_price_version_scope "
        "BEFORE INSERT ON company_point_price_versions FOR EACH ROW "
        "EXECUTE FUNCTION guard_company_point_price_version_scope()"
    )
    _execute(
        """
        CREATE FUNCTION guard_grant_point_price_version_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.point_price_candidate_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM company_point_price_versions v
            WHERE v.id = NEW.point_price_candidate_version_id
              AND v.company_id = NEW.company_id AND v.grant_id = NEW.id
              AND v.model_id = NEW.model_id AND v.status = 'CANDIDATE'
          ) THEN RAISE EXCEPTION 'grant candidate price version scope mismatch';
          END IF;
          IF NEW.point_price_active_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM company_point_price_versions v
            WHERE v.id = NEW.point_price_active_version_id
              AND v.company_id = NEW.company_id AND v.grant_id = NEW.id
              AND v.model_id = NEW.model_id AND v.status = 'ACTIVE'
          ) THEN RAISE EXCEPTION 'grant active price version scope mismatch';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_grant_point_price_version_scope "
        "BEFORE INSERT OR UPDATE ON company_model_grants FOR EACH ROW "
        "EXECUTE FUNCTION guard_grant_point_price_version_scope()"
    )
    _execute(
        """
        CREATE FUNCTION guard_company_point_ledger_task_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.task_id IS NULL AND NEW.kind NOT IN ('MIGRATION', 'CREDIT'))
             OR (NEW.task_id IS NOT NULL
                 AND NEW.kind NOT IN ('RESERVE', 'SETTLE', 'RELEASE')) THEN
            RAISE EXCEPTION 'company point ledger task shape mismatch';
          END IF;
          IF NEW.task_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM generation_tasks t
            WHERE t.id = NEW.task_id AND t.company_id = NEW.company_id
              AND t.personal_workspace_id IS NULL
              AND t.billing_unit = 'POINT' AND t.billing_version = 2
          ) THEN
            RAISE EXCEPTION 'company point ledger task scope mismatch';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_company_point_ledger_task_scope "
        "BEFORE INSERT ON company_point_ledger_entries FOR EACH ROW "
        "EXECUTE FUNCTION guard_company_point_ledger_task_scope()"
    )
    _execute(
        """
        CREATE FUNCTION guard_task_timeout_point_ledger_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.company_point_ledger_entry_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM company_point_ledger_entries e
            JOIN generation_tasks t ON t.id = NEW.task_id
            WHERE e.id = NEW.company_point_ledger_entry_id
              AND e.company_id = NEW.company_id
              AND e.task_id = NEW.task_id
              AND ((NEW.released_points > 0 AND e.kind = 'RELEASE'
                    AND e.amount_points = NEW.released_points)
                OR (NEW.released_points = 0 AND e.kind = 'SETTLE'))
              AND t.company_id = NEW.company_id
              AND t.billing_unit = 'POINT'
              AND t.billing_version = 2
          ) THEN
            RAISE EXCEPTION 'task timeout point ledger scope mismatch';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_task_timeout_point_ledger_scope "
        "BEFORE INSERT ON task_timeout_events FOR EACH ROW "
        "EXECUTE FUNCTION guard_task_timeout_point_ledger_scope()"
    )
    _execute(
        "CREATE TRIGGER trg_task_timeout_event_immutable "
        "BEFORE UPDATE OR DELETE ON task_timeout_events FOR EACH ROW "
        "EXECUTE FUNCTION reject_company_point_ledger_mutation()"
    )
    _execute(
        """
        CREATE FUNCTION guard_company_point_lot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'company point lots are durable';
          END IF;
          IF NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
             OR NEW.original_points IS DISTINCT FROM OLD.original_points
             OR NEW.cash_basis_cents IS DISTINCT FROM OLD.cash_basis_cents
             OR NEW.subsidy_cents IS DISTINCT FROM OLD.subsidy_cents
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR NEW.settled_points < OLD.settled_points THEN
            RAISE EXCEPTION 'company point lot immutable evidence changed';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_company_point_lot_guard "
        "BEFORE UPDATE OR DELETE ON company_point_lots "
        "FOR EACH ROW EXECUTE FUNCTION guard_company_point_lot_mutation()"
    )
    _execute(
        """
        CREATE FUNCTION guard_task_point_allocation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE valid_scope boolean;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'point allocations are durable';
          END IF;
          IF TG_OP = 'UPDATE' AND (
             NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.task_id IS DISTINCT FROM OLD.task_id
             OR NEW.lot_id IS DISTINCT FROM OLD.lot_id
             OR NEW.allocated_points IS DISTINCT FROM OLD.allocated_points
             OR NEW.settled_points < OLD.settled_points
             OR NEW.released_points < OLD.released_points) THEN
            RAISE EXCEPTION 'point allocation identity is immutable';
          END IF;
          SELECT EXISTS (
            SELECT 1 FROM generation_tasks t
            JOIN company_point_lots l ON l.id = NEW.lot_id
            WHERE t.id = NEW.task_id
              AND t.company_id = NEW.company_id
              AND t.personal_workspace_id IS NULL
              AND t.billing_unit = 'POINT'
              AND t.billing_version = 2
              AND l.company_id = NEW.company_id
          ) INTO valid_scope;
          IF NOT valid_scope THEN
            RAISE EXCEPTION 'point allocation scope mismatch';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_task_point_allocation_guard "
        "BEFORE INSERT OR UPDATE OR DELETE ON task_point_lot_allocations "
        "FOR EACH ROW EXECUTE FUNCTION guard_task_point_allocation()"
    )
    _execute(
        """
        CREATE FUNCTION guard_legacy_company_billing_write()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_company_id text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            target_company_id := OLD.company_id;
          ELSE
            target_company_id := NEW.company_id;
          END IF;
          IF EXISTS (SELECT 1 FROM companies c
                     WHERE c.id = target_company_id AND c.billing_version = 2) THEN
            RAISE EXCEPTION 'legacy cents billing is read-only after migration';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_legacy_ledger_block_v2 "
        "BEFORE INSERT ON ledger_entries FOR EACH ROW "
        "EXECUTE FUNCTION guard_legacy_company_billing_write()"
    )
    _execute(
        "CREATE TRIGGER trg_legacy_wallet_block_v2 "
        "BEFORE UPDATE OR DELETE ON wallet_accounts FOR EACH ROW "
        "EXECUTE FUNCTION guard_legacy_company_billing_write()"
    )
    _execute(
        """
        CREATE FUNCTION guard_task_billing_contract()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE company_version integer;
        BEGIN
          IF TG_OP = 'UPDATE' AND (
             NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.personal_workspace_id IS DISTINCT FROM OLD.personal_workspace_id
             OR NEW.billing_unit IS DISTINCT FROM OLD.billing_unit
             OR NEW.billing_version IS DISTINCT FROM OLD.billing_version
             OR NEW.quote_cents IS DISTINCT FROM OLD.quote_cents
             OR NEW.quote_points IS DISTINCT FROM OLD.quote_points) THEN
            RAISE EXCEPTION 'task billing contract is immutable';
          END IF;
          IF TG_OP = 'INSERT' AND NEW.company_id IS NOT NULL THEN
            SELECT billing_version INTO company_version FROM companies
            WHERE id = NEW.company_id;
            IF NOT ((company_version = 1 AND NEW.billing_unit = 'CNY_CENT'
                     AND NEW.billing_version = 1)
                    OR (company_version = 2 AND NEW.billing_unit = 'POINT'
                        AND NEW.billing_version = 2)) THEN
              RAISE EXCEPTION 'task billing contract mismatches company';
            END IF;
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_task_billing_contract_guard "
        "BEFORE INSERT OR UPDATE ON generation_tasks FOR EACH ROW "
        "EXECUTE FUNCTION guard_task_billing_contract()"
    )
    _execute(
        """
        CREATE FUNCTION guard_company_billing_downgrade()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.billing_version = 2 AND NEW.billing_version <> 2 THEN
            RAISE EXCEPTION 'company billing version cannot downgrade';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    _execute(
        "CREATE TRIGGER trg_company_billing_no_downgrade "
        "BEFORE UPDATE OF billing_version ON companies FOR EACH ROW "
        "EXECUTE FUNCTION guard_company_billing_downgrade()"
    )


def _drop_postgres_guards() -> None:
    for table_name, trigger_name in (
        ("company_point_ledger_entries", "trg_company_point_ledger_immutable"),
        (
            "company_point_price_versions",
            "trg_company_point_price_version_immutable",
        ),
        (
            "company_point_price_versions",
            "trg_company_point_price_version_scope",
        ),
        ("company_model_grants", "trg_grant_point_price_version_scope"),
        (
            "company_point_ledger_entries",
            "trg_company_point_ledger_task_scope",
        ),
        ("task_timeout_events", "trg_task_timeout_point_ledger_scope"),
        ("task_timeout_events", "trg_task_timeout_event_immutable"),
        ("company_point_lots", "trg_company_point_lot_guard"),
        ("task_point_lot_allocations", "trg_task_point_allocation_guard"),
        ("ledger_entries", "trg_legacy_ledger_block_v2"),
        ("wallet_accounts", "trg_legacy_wallet_block_v2"),
        ("generation_tasks", "trg_task_billing_contract_guard"),
        ("companies", "trg_company_billing_no_downgrade"),
    ):
        _execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
    for function_name in (
        "reject_company_point_ledger_mutation",
        "guard_company_point_price_version_scope",
        "guard_grant_point_price_version_scope",
        "guard_company_point_ledger_task_scope",
        "guard_task_timeout_point_ledger_scope",
        "guard_company_point_lot_mutation",
        "guard_task_point_allocation",
        "guard_legacy_company_billing_write",
        "guard_task_billing_contract",
        "guard_company_billing_downgrade",
    ):
        _execute(f"DROP FUNCTION IF EXISTS {function_name}()")


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v11()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v11)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v11,
        )

    with op.batch_alter_table("companies") as batch:
        batch.add_column(
            sa.Column(
                "billing_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.create_check_constraint(
            "ck_company_billing_version", "billing_version IN (1, 2)"
        )

    with op.batch_alter_table("company_model_grants") as batch:
        batch.drop_constraint("ck_grant_exactly_one_price", type_="check")
        batch.add_column(sa.Column("price_per_second_points", sa.BigInteger()))
        batch.add_column(sa.Column("price_per_item_points", sa.BigInteger()))
        batch.add_column(
            sa.Column("point_price_candidate_per_second", sa.BigInteger())
        )
        batch.add_column(sa.Column("point_price_candidate_per_item", sa.BigInteger()))
        batch.add_column(
            sa.Column("point_price_candidate_revision", sa.String(length=71))
        )
        batch.add_column(
            sa.Column("point_price_candidate_created_at", sa.DateTime(timezone=True))
        )
        batch.add_column(
            sa.Column("point_price_candidate_version_id", sa.String(length=36))
        )
        batch.add_column(
            sa.Column("point_price_active_version_id", sa.String(length=36))
        )
        batch.create_check_constraint(
            "ck_grant_second_points_positive",
            "price_per_second_points IS NULL OR price_per_second_points > 0",
        )
        batch.create_check_constraint(
            "ck_grant_item_points_positive",
            "price_per_item_points IS NULL OR price_per_item_points > 0",
        )
        batch.create_check_constraint("ck_grant_exactly_one_price", _GRANT_PRICE_V2)
        batch.create_check_constraint(
            "ck_grant_candidate_second_points_positive",
            "point_price_candidate_per_second IS NULL "
            "OR point_price_candidate_per_second > 0",
        )
        batch.create_check_constraint(
            "ck_grant_candidate_item_points_positive",
            "point_price_candidate_per_item IS NULL "
            "OR point_price_candidate_per_item > 0",
        )
        batch.create_check_constraint(
            "ck_grant_candidate_one_mode",
            "point_price_candidate_per_second IS NULL "
            "OR point_price_candidate_per_item IS NULL",
        )

    op.create_table(
        "company_point_price_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "grant_id",
            sa.String(length=36),
            sa.ForeignKey("company_model_grants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(length=36),
            sa.ForeignKey("model_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CANDIDATE",
                "ACTIVE",
                "SUPERSEDED",
                name="pointpriceversionstatus",
                native_enum=False,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("billing_mode", sa.String(length=20), nullable=False),
        sa.Column("unit_price_points", sa.BigInteger(), nullable=False),
        sa.Column("source_price_cents", sa.BigInteger()),
        sa.Column("formula_version", sa.String(length=80), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "supersedes_version_id",
            sa.String(length=36),
            sa.ForeignKey("company_point_price_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_by_system_key", sa.String(length=120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('CANDIDATE', 'ACTIVE', 'SUPERSEDED')",
            name="ck_company_point_price_version_status",
        ),
        sa.CheckConstraint(
            "billing_mode IN ('per_second', 'per_item')",
            name="ck_company_point_price_version_mode",
        ),
        sa.CheckConstraint(
            "unit_price_points > 0",
            name="ck_company_point_price_version_positive",
        ),
        sa.CheckConstraint(
            "source_price_cents IS NULL OR source_price_cents > 0",
            name="ck_company_point_price_version_source_positive",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_company_point_price_version_sha_length",
        ),
        sa.CheckConstraint(
            "(created_by_user_id IS NOT NULL AND created_by_system_key IS NULL) OR "
            "(created_by_user_id IS NULL AND created_by_system_key IS NOT NULL)",
            name="ck_company_point_price_version_actor",
        ),
    )
    op.create_index(
        "ix_company_point_price_version_grant_created",
        "company_point_price_versions",
        ["grant_id", "created_at", "id"],
    )
    op.create_index(
        "ix_company_point_price_versions_company_id",
        "company_point_price_versions",
        ["company_id"],
    )
    op.create_index(
        "ix_company_point_price_versions_grant_id",
        "company_point_price_versions",
        ["grant_id"],
    )
    op.create_index(
        "ix_company_point_price_versions_model_id",
        "company_point_price_versions",
        ["model_id"],
    )
    with op.batch_alter_table("company_model_grants") as batch:
        batch.create_foreign_key(
            "fk_grant_point_candidate_version",
            "company_point_price_versions",
            ["point_price_candidate_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_grant_point_active_version",
            "company_point_price_versions",
            ["point_price_active_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("generation_tasks") as batch:
        batch.drop_constraint("ck_task_scope_quote", type_="check")
        batch.add_column(
            sa.Column(
                "billing_unit",
                sa.Enum(
                    "CNY_CENT",
                    "POINT",
                    name="billingunit",
                    native_enum=False,
                    validate_strings=True,
                ),
                nullable=False,
                server_default=sa.text("'CNY_CENT'"),
            )
        )
        batch.add_column(
            sa.Column(
                "billing_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
    _execute(
        "UPDATE generation_tasks SET billing_unit='POINT', billing_version=2 "
        "WHERE personal_workspace_id IS NOT NULL"
    )
    with op.batch_alter_table("generation_tasks") as batch:
        batch.create_check_constraint("ck_task_scope_quote", _TASK_SCOPE_QUOTE_V2)
        batch.create_check_constraint(
            "ck_task_billing_version", "billing_version IN (1, 2)"
        )

    op.create_table(
        "company_point_wallet_accounts",
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("available_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_points", sa.BigInteger(), nullable=False),
        sa.Column("migration_idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("migrated_from_available_cents", sa.BigInteger(), nullable=False),
        sa.Column("migration_remainder_cents", sa.Integer(), nullable=False),
        sa.Column("migration_rounding_grant_points", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "available_points >= 0", name="ck_company_point_wallet_available"
        ),
        sa.CheckConstraint(
            "reserved_points >= 0", name="ck_company_point_wallet_reserved"
        ),
        sa.CheckConstraint(
            "migrated_from_available_cents >= 0",
            name="ck_company_point_wallet_migrated_cents",
        ),
        sa.CheckConstraint(
            "migration_remainder_cents BETWEEN 0 AND 9",
            name="ck_company_point_wallet_remainder",
        ),
        sa.CheckConstraint(
            "migration_rounding_grant_points IN (0, 1)",
            name="ck_company_point_wallet_rounding_grant",
        ),
    )
    op.create_table(
        "company_point_lots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_kind",
            sa.Enum(
                "PURCHASED",
                "CONTRACT",
                "PROMOTIONAL",
                "COMPENSATION",
                "LEGACY",
                "MIGRATION_REMAINDER",
                "INTERNAL_TEST",
                name="pointlotsourcekind",
                native_enum=False,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("original_points", sa.BigInteger(), nullable=False),
        sa.Column("available_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_points", sa.BigInteger(), nullable=False),
        sa.Column("settled_points", sa.BigInteger(), nullable=False),
        sa.Column("cash_basis_cents", sa.BigInteger(), nullable=False),
        sa.Column("subsidy_cents", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "company_id", "idempotency_key", name="uq_company_point_lot_idempotency"
        ),
        sa.CheckConstraint(
            "source_kind IN ('PURCHASED', 'CONTRACT', 'PROMOTIONAL', "
            "'COMPENSATION', 'LEGACY', 'MIGRATION_REMAINDER', 'INTERNAL_TEST')",
            name="ck_company_point_lot_source_kind",
        ),
        sa.CheckConstraint("original_points > 0", name="ck_company_point_lot_original"),
        sa.CheckConstraint("available_points >= 0", name="ck_company_point_lot_available"),
        sa.CheckConstraint("reserved_points >= 0", name="ck_company_point_lot_reserved"),
        sa.CheckConstraint("settled_points >= 0", name="ck_company_point_lot_settled"),
        sa.CheckConstraint("cash_basis_cents >= 0", name="ck_company_point_lot_cash_basis"),
        sa.CheckConstraint("subsidy_cents >= 0", name="ck_company_point_lot_subsidy"),
        sa.CheckConstraint(
            "cash_basis_cents + subsidy_cents = original_points * 10",
            name="ck_company_point_lot_value_basis",
        ),
        sa.CheckConstraint(
            "original_points = available_points + reserved_points + settled_points",
            name="ck_company_point_lot_conservation",
        ),
    )
    op.create_index(
        "ix_company_point_lot_spend_order",
        "company_point_lots",
        ["company_id", "expires_at", "created_at", "id"],
    )
    op.create_index(
        "ix_company_point_lots_company_id",
        "company_point_lots",
        ["company_id"],
    )
    op.create_table(
        "company_point_ledger_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "MIGRATION",
                "CREDIT",
                "RESERVE",
                "SETTLE",
                "RELEASE",
                name="pointledgerkind",
                native_enum=False,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("amount_points", sa.BigInteger(), nullable=False),
        sa.Column("available_delta_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_delta_points", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        ),
        sa.Column("note", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_company_point_ledger_idempotency",
        ),
        sa.CheckConstraint("amount_points >= 0", name="ck_company_point_ledger_amount"),
        sa.CheckConstraint(
            "kind IN ('MIGRATION', 'CREDIT', 'RESERVE', 'SETTLE', 'RELEASE')",
            name="ck_company_point_ledger_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'MIGRATION' AND amount_points >= 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = 0) OR "
            "(kind = 'CREDIT' AND amount_points > 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = 0) OR "
            "(kind = 'RESERVE' AND amount_points > 0 "
            "AND available_delta_points = -amount_points "
            "AND reserved_delta_points = amount_points) OR "
            "(kind = 'SETTLE' AND amount_points > 0 "
            "AND available_delta_points = 0 "
            "AND reserved_delta_points = -amount_points) OR "
            "(kind = 'RELEASE' AND amount_points > 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = -amount_points)",
            name="ck_company_point_ledger_delta_shape",
        ),
    )
    op.create_index(
        "ix_company_point_ledger_created",
        "company_point_ledger_entries",
        ["company_id", "created_at", "id"],
    )
    op.create_index(
        "ix_company_point_ledger_entries_company_id",
        "company_point_ledger_entries",
        ["company_id"],
    )
    op.create_index(
        "ix_company_point_ledger_entries_task_id",
        "company_point_ledger_entries",
        ["task_id"],
    )
    with op.batch_alter_table("task_timeout_events") as batch:
        batch.drop_constraint("ck_task_timeout_scope", type_="check")
        batch.add_column(
            sa.Column("company_point_ledger_entry_id", sa.String(length=36))
        )
        batch.create_foreign_key(
            "fk_task_timeout_company_point_ledger",
            "company_point_ledger_entries",
            ["company_point_ledger_entry_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_task_timeout_company_point_ledger",
            ["company_point_ledger_entry_id"],
        )
        batch.create_check_constraint(
            "ck_task_timeout_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND personal_ledger_entry_id IS NULL AND ("
            "(released_cents = 0 AND released_points = 0 "
            "AND (ledger_entry_id IS NULL "
            "OR company_point_ledger_entry_id IS NULL)) OR "
            "(released_cents > 0 AND released_points = 0 "
            "AND ledger_entry_id IS NOT NULL "
            "AND company_point_ledger_entry_id IS NULL) OR "
            "(released_cents = 0 AND released_points > 0 "
            "AND ledger_entry_id IS NULL "
            "AND company_point_ledger_entry_id IS NOT NULL))) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND released_cents = 0 AND ledger_entry_id IS NULL "
            "AND company_point_ledger_entry_id IS NULL)",
        )
    op.create_table(
        "task_point_lot_allocations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "lot_id",
            sa.String(length=36),
            sa.ForeignKey("company_point_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("allocated_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_points", sa.BigInteger(), nullable=False),
        sa.Column("settled_points", sa.BigInteger(), nullable=False),
        sa.Column("released_points", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "lot_id", name="uq_task_point_lot_allocation"),
        sa.CheckConstraint("allocated_points > 0", name="ck_task_point_allocation_total"),
        sa.CheckConstraint("reserved_points >= 0", name="ck_task_point_allocation_reserved"),
        sa.CheckConstraint("settled_points >= 0", name="ck_task_point_allocation_settled"),
        sa.CheckConstraint("released_points >= 0", name="ck_task_point_allocation_released"),
        sa.CheckConstraint(
            "allocated_points = reserved_points + settled_points + released_points",
            name="ck_task_point_allocation_conservation",
        ),
    )
    op.create_index(
        "ix_task_point_lot_allocation_task",
        "task_point_lot_allocations",
        ["task_id", "id"],
    )
    op.create_index(
        "ix_task_point_lot_allocations_company_id",
        "task_point_lot_allocations",
        ["company_id"],
    )

    if connection.dialect.name == "sqlite":
        _create_sqlite_guards()
    elif connection.dialect.name == "postgresql":
        _create_postgres_guards()
        if protected_postgres:
            _apply_company_points_acl()
            evidence = collect_platform_database_evidence(
                connection,
                policy=policy_v11,
            )
            validate_platform_database_acl_evidence(
                evidence,
                require_head=False,
                policy=policy_v11,
            )


def downgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v11()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v11)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v11,
        )
    migrated = int(
        connection.scalar(
            sa.text("SELECT count(*) FROM companies WHERE billing_version = 2")
        )
        or 0
    )
    if migrated:
        raise RuntimeError(
            "0046 downgrade would erase active company point billing evidence"
        )
    if connection.dialect.name == "sqlite":
        _drop_sqlite_guards()
    elif connection.dialect.name == "postgresql":
        _drop_postgres_guards()

    op.drop_index(
        "ix_task_point_lot_allocations_company_id",
        table_name="task_point_lot_allocations",
    )
    op.drop_index(
        "ix_task_point_lot_allocation_task",
        table_name="task_point_lot_allocations",
    )
    op.drop_table("task_point_lot_allocations")
    with op.batch_alter_table("task_timeout_events") as batch:
        batch.drop_constraint("ck_task_timeout_scope", type_="check")
        batch.drop_constraint(
            "uq_task_timeout_company_point_ledger", type_="unique"
        )
        batch.drop_constraint(
            "fk_task_timeout_company_point_ledger", type_="foreignkey"
        )
        batch.drop_column("company_point_ledger_entry_id")
        batch.create_check_constraint(
            "ck_task_timeout_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND released_points = 0 AND personal_ledger_entry_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND released_cents = 0 AND ledger_entry_id IS NULL)",
        )
    op.drop_index(
        "ix_company_point_ledger_entries_task_id",
        table_name="company_point_ledger_entries",
    )
    op.drop_index(
        "ix_company_point_ledger_entries_company_id",
        table_name="company_point_ledger_entries",
    )
    op.drop_index(
        "ix_company_point_ledger_created",
        table_name="company_point_ledger_entries",
    )
    op.drop_table("company_point_ledger_entries")
    op.drop_index(
        "ix_company_point_lot_spend_order", table_name="company_point_lots"
    )
    op.drop_table("company_point_lots")
    op.drop_table("company_point_wallet_accounts")
    with op.batch_alter_table("company_model_grants") as batch:
        batch.drop_constraint("fk_grant_point_active_version", type_="foreignkey")
        batch.drop_constraint("fk_grant_point_candidate_version", type_="foreignkey")
    op.drop_index(
        "ix_company_point_price_versions_model_id",
        table_name="company_point_price_versions",
    )
    op.drop_index(
        "ix_company_point_price_versions_grant_id",
        table_name="company_point_price_versions",
    )
    op.drop_index(
        "ix_company_point_price_versions_company_id",
        table_name="company_point_price_versions",
    )
    op.drop_index(
        "ix_company_point_price_version_grant_created",
        table_name="company_point_price_versions",
    )
    op.drop_table("company_point_price_versions")

    with op.batch_alter_table("generation_tasks") as batch:
        batch.drop_constraint("ck_task_billing_version", type_="check")
        batch.drop_constraint("ck_task_scope_quote", type_="check")
        batch.drop_column("billing_version")
        batch.drop_column("billing_unit")
        batch.create_check_constraint(
            "ck_task_scope_quote",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND quote_cents > 0 AND quote_points IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND quote_cents IS NULL AND quote_points > 0)",
        )

    with op.batch_alter_table("company_model_grants") as batch:
        for constraint_name in (
            "ck_grant_candidate_one_mode",
            "ck_grant_candidate_item_points_positive",
            "ck_grant_candidate_second_points_positive",
            "ck_grant_exactly_one_price",
            "ck_grant_item_points_positive",
            "ck_grant_second_points_positive",
        ):
            batch.drop_constraint(constraint_name, type_="check")
        batch.drop_column("point_price_candidate_created_at")
        batch.drop_column("point_price_active_version_id")
        batch.drop_column("point_price_candidate_version_id")
        batch.drop_column("point_price_candidate_revision")
        batch.drop_column("point_price_candidate_per_item")
        batch.drop_column("point_price_candidate_per_second")
        batch.drop_column("price_per_item_points")
        batch.drop_column("price_per_second_points")
        batch.create_check_constraint(
            "ck_grant_exactly_one_price",
            "(price_per_second_cents IS NOT NULL AND price_per_item_cents IS NULL) "
            "OR (price_per_second_cents IS NULL AND price_per_item_cents IS NOT NULL)",
        )

    with op.batch_alter_table("companies") as batch:
        batch.drop_constraint("ck_company_billing_version", type_="check")
        batch.drop_column("billing_version")

    if protected_postgres:
        evidence = collect_v10_database_evidence(
            connection,
            policy=policy_v10,
        )
        validate_v10_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v10,
        )
