from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from platform_api import database_privileges_v13 as policy_v13
from platform_api import database_privileges_v14 as policy_v14


PREVIOUS_HEAD = "0047_owner_self_product_context"


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    return config


def _head(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(text("SELECT version_num FROM alembic_version"))
        )


def _seed_personal_wallet(engine) -> None:
    at = "2026-08-30 10:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,is_platform_admin,"
                "account_type,status,auth_version,created_at,updated_at) VALUES "
                "('commercial-personal','commercial@example.test','Commercial',"
                "false,'PERSONAL','ACTIVE',1,:at,:at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO personal_workspaces "
                "(id,user_id,active,owner_self_identity_id,created_at,updated_at) "
                "VALUES ('commercial-workspace','commercial-personal',true,NULL,:at,:at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO personal_wallet_accounts "
                "(workspace_id,available_points,reserved_points,created_at,updated_at) "
                "VALUES ('commercial-workspace',30,7,:at,:at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO model_definitions "
                "(id,slug,display_name,provider_key,billing_mode,capability_version,"
                "active,created_at,updated_at) VALUES "
                "('commercial-model','commercial-model','Commercial Model','test',"
                "'per_item',1,true,:at,:at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO generation_tasks "
                "(id,company_id,personal_workspace_id,user_id,model_id,idempotency_key,"
                "request_fingerprint,status,request_payload,billing_unit,billing_version,"
                "quote_cents,quote_points,pricing_snapshot,capability_snapshot,"
                "reserved_cents,reserved_points,actual_cost_cents,actual_cost_points,"
                "created_at,updated_at) VALUES "
                "('commercial-task',NULL,'commercial-workspace','commercial-personal',"
                "'commercial-model','commercial-task-key',:fingerprint,'QUEUED','{}',"
                "'POINT',2,NULL,7,'{}','{}',0,7,NULL,NULL,:at,:at)"
            ),
            {"fingerprint": "b" * 64, "at": at},
        )


def test_commercial_billing_upgrade_backfills_personal_lots_and_guards_facts(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "commercial-billing.db"
    config = _config(project_root, database_path)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _seed_personal_wallet(engine)
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == ScriptDirectory.from_config(config).get_current_head()
    assert policy_v14.CLOSURE_TABLES <= set(inspect(engine).get_table_names())

    with engine.connect() as connection:
        lot = connection.execute(
            text(
                "SELECT source_kind,original_points,available_points,reserved_points,"
                "cash_basis_cents,receivable_basis_cents,subsidy_cents,refundable "
                "FROM personal_point_lots WHERE workspace_id='commercial-workspace'"
            )
        ).one()
        assert tuple(lot) == ("LEGACY", 37, 30, 7, 0, 0, 370, 0)
        allocation = connection.execute(
            text(
                "SELECT allocated_points,reserved_points,settled_points,released_points "
                "FROM personal_task_point_lot_allocations WHERE task_id='commercial-task'"
            )
        ).one()
        assert tuple(allocation) == (7, 7, 0, 0)
        trigger_names = set(
            connection.scalars(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        )
        assert {
            "trg_payment_webhook_receipts_no_update",
            "trg_payment_webhook_receipts_no_delete",
            "trg_point_lot_settlement_value_allocations_no_update",
            "trg_personal_point_lot_no_delete",
        } <= trigger_names

    source_document_bytes = b"a" * 100
    source_document_sha256 = hashlib.sha256(source_document_bytes).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO payment_settlement_batches "
                "(id,provider,merchant_account,source_kind,period_start,period_end,"
                "provider_document_id,source_document_sha256,source_document_bytes,source_object_key,"
                "source_object_version,source_size_bytes,verification_method,verified_at,"
                "parser_version,lines_sha256,currency,line_count,gross_total_cents,"
                "fee_total_cents,net_total_cents,imported_at) VALUES "
                "('batch-a','test','merchant-a','PSP_STATEMENT','2026-08-30 00:00:00',"
                "'2026-08-31 00:00:00','doc-a',:sha,:document_bytes,'settlements/doc-a','v1',100,"
                "'signed_file','2026-08-31 01:00:00','v1',:lines_sha,'CNY',1,100,2,98,"
                "'2026-08-31 01:00:00')"
            ),
            {
                "sha": source_document_sha256,
                "document_bytes": source_document_bytes,
                "lines_sha": "c" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO payment_settlement_entries "
                "(id,batch_id,provider,merchant_account,provider_line_id,"
                "provider_transaction_id,line_type,gross_amount_cents,fee_amount_cents,"
                "net_amount_cents,currency,occurred_at,source_document_sha256,created_at) "
                "VALUES ('settlement-a','batch-a','test','merchant-a','line-a','txn-a','capture',"
                "100,2,98,'CNY','2026-08-30 10:00:00',:sha,'2026-08-30 10:00:00')"
            ),
            {"sha": source_document_sha256},
        )
    with pytest.raises(DBAPIError, match="commercial billing fact is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE payment_settlement_entries SET net_amount_cents=97 "
                    "WHERE id='settlement-a'"
                )
            )
    with pytest.raises(DBAPIError, match="payment closure fact is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE payment_settlement_batches SET source_document_bytes=:content "
                    "WHERE id='batch-a'"
                ),
                {"content": b"b" * 100},
            )
    engine.dispose()


def test_commercial_billing_downgrade_and_reupgrade_are_reversible(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "commercial-billing-roundtrip.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "0048_commercial_billing")
    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == PREVIOUS_HEAD
    assert policy_v13.COMMERCIAL_TABLES.isdisjoint(
        inspect(engine).get_table_names()
    )
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == ScriptDirectory.from_config(config).get_current_head()
    engine.dispose()


def test_closure_upgrade_refuses_unbound_legacy_settlement_without_losing_history(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "legacy-unbound-settlement.db"
    config = _config(project_root, database_path)
    command.upgrade(config, "0048_commercial_billing")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO payment_settlement_entries "
                "(id,provider,merchant_account,provider_line_id,provider_transaction_id,"
                "line_type,gross_amount_cents,fee_amount_cents,net_amount_cents,currency,"
                "occurred_at,source_document_sha256,created_at) VALUES "
                "('legacy-line','test','merchant-a','legacy-line','legacy-txn','capture',"
                "100,0,100,'CNY','2026-08-30 10:00:00',:sha,'2026-08-30 10:00:00')"
            ),
            {"sha": "a" * 64},
        )
    with pytest.raises(RuntimeError, match="history-preserving migration plan"):
        command.upgrade(config, "head")
    assert _head(engine) == "0048_commercial_billing"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM payment_settlement_entries")) == 1
    assert "payment_settlement_batches" not in inspect(engine).get_table_names()
    engine.dispose()


def test_closure_upgrade_does_not_invent_consent_for_legacy_automatic_orders(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "legacy-unbound-automatic-order.db"
    config = _config(project_root, database_path)
    command.upgrade(config, "0048_commercial_billing")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _seed_personal_wallet(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO payment_orders "
                "(id,company_id,personal_workspace_id,created_by_user_id,purpose,"
                "purpose_reference_id,provider,merchant_account,provider_order_id,status,"
                "currency,amount_cents,points,captured_amount_cents,refunded_amount_cents,"
                "disputed_amount_cents,fee_amount_cents,idempotency_key,request_fingerprint,"
                "automatic,provider_customer_reference,created_at,updated_at) VALUES "
                "('legacy-auto',NULL,'commercial-workspace','commercial-personal',"
                "'POINT_PURCHASE',NULL,'test','merchant-a',NULL,'CREATED','CNY',100,10,"
                "0,0,0,0,'legacy-auto-key',:sha,true,'legacy-customer',"
                "'2026-08-30 10:00:00','2026-08-30 10:00:00')"
            ),
            {"sha": "a" * 64},
        )
    with pytest.raises(RuntimeError, match="no payment mandate is inferred"):
        command.upgrade(config, "head")
    assert _head(engine) == "0048_commercial_billing"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM payment_orders")) == 1
    assert "payment_mandates" not in inspect(engine).get_table_names()
    engine.dispose()
