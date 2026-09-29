from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_api import database_privileges as privileges
from platform_api import database_privileges_behavior_v1 as behavior_v1
from platform_api import database_privileges_behavior_v2 as behavior_v2
from platform_api import database_privileges_behavior_v3 as behavior_v3
from platform_api import database_privileges_behavior_v4 as behavior_v4
from platform_api import database_privileges_behavior_v5 as behavior_v5
from platform_api import database_privileges_behavior_v6 as behavior_v6
from platform_api import database_privileges_behavior_v7 as behavior_v7
from platform_api import database_privileges_behavior_v8 as behavior_v8
from platform_api import database_privileges_behavior_v9 as behavior_v9
from platform_api import database_privileges_behavior_v10 as behavior_v10
from platform_api import database_privileges_behavior_v11 as behavior_v11
from platform_api import database_privileges_behavior_v12 as behavior_v12
from platform_api import database_privileges_behavior_v13 as behavior_v13
from platform_api import database_privileges_behavior_v14 as behavior_v14
from platform_api import database_privileges_behavior_v15 as behavior_v15
from platform_api import database_privileges_behavior_v16 as behavior_v16
from platform_api import database_privileges_behavior_v17 as behavior_v17
from platform_api import database_privileges_behavior_v18 as behavior_v18
from platform_api import database_privileges_behavior_v19 as behavior_v19
from platform_api import database_privileges_behavior_v20 as behavior_v20
from platform_api import database_privileges_behavior_v21 as behavior_v21
from platform_api import database_privileges_behavior_v22 as behavior_v22
from platform_api import database_privileges_behavior_v23 as behavior_v23
from platform_api import database_privileges_behavior_v24 as behavior_v24
from platform_api import database_privileges_behavior_v25 as behavior_v25
from platform_api import database_privileges_behavior_v26 as behavior_v26
from platform_api import database_privileges_behavior_v27 as behavior_v27
from platform_api import database_privileges_behavior_v28 as behavior_v28
from platform_api import database_privileges_behavior_v29 as behavior_v29
from platform_api import database_privileges_v1 as policy_v1
from platform_api import database_privileges_v2 as policy_v2
from platform_api import database_privileges_v3 as policy_v3
from platform_api import database_privileges_v4 as policy_v4
from platform_api import database_privileges_v5 as policy_v5
from platform_api import database_privileges_v6 as policy_v6
from platform_api import database_privileges_v7 as policy_v7
from platform_api import database_privileges_v8 as policy_v8
from platform_api import database_privileges_v9 as policy_v9
from platform_api import database_privileges_v10 as policy_v10
from platform_api import database_privileges_v11 as policy_v11
from platform_api import database_privileges_v12 as policy_v12
from platform_api import database_privileges_v13 as policy_v13
from platform_api import database_privileges_v14 as policy_v14
from platform_api import database_privileges_v15 as policy_v15
from platform_api import database_privileges_v16 as policy_v16
from platform_api import database_privileges_v17 as policy_v17
from platform_api import database_privileges_v18 as policy_v18
from platform_api import database_privileges_v19 as policy_v19
from platform_api import database_privileges_v20 as policy_v20
from platform_api import database_privileges_v21 as policy_v21
from platform_api import database_privileges_v22 as policy_v22
from platform_api import database_privileges_v23 as policy_v23
from platform_api import database_privileges_v24 as policy_v24
from platform_api import database_privileges_v25 as policy_v25
from platform_api import database_privileges_v26 as policy_v26
from platform_api import database_privileges_v27 as policy_v27
from platform_api import database_privileges_v28 as policy_v28
from platform_api import database_privileges_v29 as policy_v29
from platform_api import database_privileges_v30 as policy_v30
from platform_api.database import Base
from platform_api.database_system_semantic_v1 import (
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
)
from platform_api import models  # noqa: F401 - register metadata
from platform_api import platform_admin_access_models  # noqa: F401


AUTH_TABLES = frozenset(
    {
        "account_security_events",
        "auth_sessions",
        "company_invitations",
        "external_identities",
        "oidc_login_transactions",
    }
)


def _normalized_sha256(path: Path) -> str:
    source = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_frozen_v1_policy_and_behavior_sources_are_byte_exact() -> None:
    package = Path(__file__).parents[1] / "platform_api"
    assert _normalized_sha256(package / "database_privileges_v1.py") == (
        "a4a333c9238086b9494765e17b14ccabb39712bf34e64e660962a76976e20270"
    )
    assert _normalized_sha256(package / "database_privileges_behavior_v1.py") == (
        "5c2aea413b76552842c0eccda5100502626060208771912a0424af8d85713ea3"
    )


def test_frozen_v2_policy_and_behavior_sources_are_byte_exact() -> None:
    package = Path(__file__).parents[1] / "platform_api"
    assert _normalized_sha256(package / "database_privileges_v2.py") == (
        "ccf5d0093dd5a7c387acf0d0707da6993a5cf686471eb6ec3f269387cf054f8c"
    )
    assert _normalized_sha256(package / "database_privileges_behavior_v2.py") == (
        "79a8d3bc88579861f066f4012cbd64551f68f86da189d5d4f050905c1cc4c624"
    )


def test_frozen_v3_policy_and_behavior_sources_are_byte_exact() -> None:
    package = Path(__file__).parents[1] / "platform_api"
    assert _normalized_sha256(package / "database_privileges_v3.py") == (
        "d13f7080a9e966fcd4ec578251343d2f456bad2b658dc41bd091efa3e7de50c3"
    )
    assert _normalized_sha256(package / "database_privileges_behavior_v3.py") == (
        "6e0b5563d39eabfa004514856807b1d5044ae25ca3a8277b7d8cee355e30a206"
    )


def test_frozen_v4_policy_and_behavior_sources_are_byte_exact() -> None:
    package = Path(__file__).parents[1] / "platform_api"
    assert _normalized_sha256(package / "database_privileges_v4.py") == (
        "bcfd6e55241d259e68789377097f712f19fc9141ecc038c6c012ad3a52d5aef9"
    )
    assert _normalized_sha256(package / "database_privileges_behavior_v4.py") == (
        "c3530c0bdde17df325688853387bbbf36688bf69844b0adbd20e886618c2e51f"
    )


def test_frozen_v5_policy_and_behavior_sources_are_byte_exact() -> None:
    package = Path(__file__).parents[1] / "platform_api"
    assert _normalized_sha256(package / "database_privileges_v5.py") == (
        "ca5fc6723200a0485376a4f89cda194bb6cc3590da132fefab95ce1ea581c51b"
    )
    assert _normalized_sha256(package / "database_privileges_behavior_v5.py") == (
        "3f6093534676f32fe63bad6ae874f5d4f1cd67896ae7ec86b8fd2b20a2913120"
    )


def test_runtime_facade_selects_v30_and_keeps_frozen_registry_entries() -> None:
    assert privileges.CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY is policy_v30
    assert privileges.CURRENT_PLATFORM_DATABASE_PRIVILEGE_BEHAVIOR is behavior_v29
    assert privileges.PLATFORM_ALEMBIC_HEAD == policy_v30.ALEMBIC_HEAD
    assert privileges.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY == {
        policy_v1.ALEMBIC_HEAD: (policy_v1, behavior_v1),
        policy_v2.ALEMBIC_HEAD: (policy_v2, behavior_v2),
        policy_v3.ALEMBIC_HEAD: (policy_v3, behavior_v3),
        policy_v4.ALEMBIC_HEAD: (policy_v4, behavior_v4),
        policy_v5.ALEMBIC_HEAD: (policy_v5, behavior_v5),
        policy_v6.ALEMBIC_HEAD: (policy_v6, behavior_v6),
        policy_v7.ALEMBIC_HEAD: (policy_v7, behavior_v7),
        policy_v8.ALEMBIC_HEAD: (policy_v8, behavior_v8),
        policy_v9.ALEMBIC_HEAD: (policy_v9, behavior_v9),
        policy_v10.ALEMBIC_HEAD: (policy_v10, behavior_v10),
        policy_v11.ALEMBIC_HEAD: (policy_v11, behavior_v11),
        policy_v12.ALEMBIC_HEAD: (policy_v12, behavior_v12),
        policy_v13.ALEMBIC_HEAD: (policy_v13, behavior_v13),
        policy_v14.ALEMBIC_HEAD: (policy_v14, behavior_v14),
        policy_v15.ALEMBIC_HEAD: (policy_v15, behavior_v15),
        policy_v16.ALEMBIC_HEAD: (policy_v16, behavior_v16),
        policy_v17.ALEMBIC_HEAD: (policy_v17, behavior_v17),
        policy_v18.ALEMBIC_HEAD: (policy_v18, behavior_v18),
        policy_v19.ALEMBIC_HEAD: (policy_v19, behavior_v19),
        policy_v20.ALEMBIC_HEAD: (policy_v20, behavior_v20),
        policy_v21.ALEMBIC_HEAD: (policy_v21, behavior_v21),
        policy_v22.ALEMBIC_HEAD: (policy_v22, behavior_v22),
        policy_v23.ALEMBIC_HEAD: (policy_v23, behavior_v23),
        policy_v24.ALEMBIC_HEAD: (policy_v24, behavior_v24),
        policy_v25.ALEMBIC_HEAD: (policy_v25, behavior_v25),
        policy_v26.ALEMBIC_HEAD: (policy_v26, behavior_v26),
        policy_v27.ALEMBIC_HEAD: (policy_v27, behavior_v27),
        policy_v28.ALEMBIC_HEAD: (policy_v28, behavior_v28),
        policy_v29.ALEMBIC_HEAD: (policy_v29, behavior_v29),
        policy_v30.ALEMBIC_HEAD: (policy_v30, behavior_v29),
    }
    assert policy_v1.CATALOG_SHA256 == (
        "816e9b60476fff7b6e1fc9ee6e7c5c460bf971ead1dbccb8ac8fce86e5fcffeb"
    )
    assert policy_v1.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
        "0035_operations_evidence"
    ] == (
        "efa9781128bc5319098882e861249911f8af50a03884f997e087995c056dea8f"
    )
    assert policy_v2.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
        policy_v1.ALEMBIC_HEAD
    ] == (
        "816e9b60476fff7b6e1fc9ee6e7c5c460bf971ead1dbccb8ac8fce86e5fcffeb"
    )
    assert set(policy_v2.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD) == {
        policy_v1.ALEMBIC_HEAD,
        policy_v2.ALEMBIC_HEAD,
    }
    assert policy_v2.CATALOG_SHA256 == (
        "7427bb1db832d08d75b86d426b63c867464358b3a7d74b07bd7e659421db5f0f"
    )
    assert policy_v3.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v2.ALEMBIC_HEAD: policy_v2.CATALOG_SHA256,
        policy_v3.ALEMBIC_HEAD: policy_v3.CATALOG_SHA256,
    }
    assert policy_v3.CATALOG_SHA256 == (
        "6fd6420e20423ac99e72262f7186a386e02ae6a98d613755f12ddc89f32ed71b"
    )
    assert policy_v4.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v3.ALEMBIC_HEAD: policy_v3.CATALOG_SHA256,
        policy_v4.ALEMBIC_HEAD: policy_v4.CATALOG_SHA256,
    }
    assert policy_v4.CATALOG_SHA256 == (
        "c9a154d6c87c714d6af4826bb43ce7fae56f73322066f399cee526d18280b757"
    )
    assert policy_v5.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v4.ALEMBIC_HEAD: policy_v4.CATALOG_SHA256,
        policy_v5.ALEMBIC_HEAD: policy_v5.CATALOG_SHA256,
    }
    assert policy_v5.CATALOG_SHA256 == (
        "ecd5b3faae20595e66396c59d37327d1e6e5b742c3d70697aaf6f109866591e6"
    )
    assert policy_v6.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v5.ALEMBIC_HEAD: (
            "15f8e992c8e6a88d587fe7383fa1b390da3696834c11acf255856520a6265cfa"
        ),
        policy_v6.ALEMBIC_HEAD: policy_v6.CATALOG_SHA256,
    }
    assert policy_v6.CATALOG_SHA256 == (
        "397e6923d44a7c68ab462d50895b95715f0edf7ffb7fd965227ce8ef02075ae9"
    )
    assert policy_v7.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v6.ALEMBIC_HEAD: policy_v6.CATALOG_SHA256,
        policy_v7.ALEMBIC_HEAD: policy_v7.CATALOG_SHA256,
    }
    assert policy_v7.CATALOG_SHA256 == (
        "2d8cf3f6ad44338ed77ee0506c8ec058fca02ab84b64186ea09df17a5387d9e8"
    )
    assert policy_v8.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v7.ALEMBIC_HEAD: policy_v7.CATALOG_SHA256,
        policy_v8.ALEMBIC_HEAD: policy_v8.CATALOG_SHA256,
    }
    assert policy_v8.CATALOG_SHA256 == policy_v7.CATALOG_SHA256
    assert policy_v9.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v8.ALEMBIC_HEAD: policy_v8.CATALOG_SHA256,
        policy_v9.ALEMBIC_HEAD: policy_v9.CATALOG_SHA256,
    }
    assert policy_v9.CATALOG_SHA256 == (
        "64640e8ccf7069fc6ca0773af64def56babfdb80101ea9cd22e6b8e7fc00c167"
    )
    assert policy_v10.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v9.ALEMBIC_HEAD: policy_v9.CATALOG_SHA256,
        policy_v10.ALEMBIC_HEAD: policy_v10.CATALOG_SHA256,
    }
    assert policy_v10.CATALOG_SHA256 == (
        "7ce8849ecc4be298fe9889bdeaeb7ea17932a51c9ff9eeb024cc55bbcad44142"
    )
    assert policy_v10.CATALOG_SHA256 != policy_v10.UNQUALIFIED_CATALOG_SHA256
    assert policy_v11.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v10.ALEMBIC_HEAD: policy_v10.CATALOG_SHA256,
        policy_v11.ALEMBIC_HEAD: policy_v11.CATALOG_SHA256,
    }
    assert policy_v11.CATALOG_SHA256 == (
        "d4b70386e7592c884394b45d5d0ee00ba3ca7d31cc2881d237ba071f358c142c"
    )
    assert policy_v11.CATALOG_SHA256 != policy_v11.UNQUALIFIED_CATALOG_SHA256
    assert policy_v12.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v11.ALEMBIC_HEAD: policy_v11.CATALOG_SHA256,
        policy_v12.ALEMBIC_HEAD: policy_v12.CATALOG_SHA256,
    }
    assert policy_v12.CATALOG_SHA256 == "0" * 64
    assert policy_v13.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v12.ALEMBIC_HEAD: policy_v12.CATALOG_SHA256,
        policy_v13.ALEMBIC_HEAD: policy_v13.CATALOG_SHA256,
    }
    assert policy_v13.CATALOG_SHA256 == "0" * 64
    assert policy_v14.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v13.ALEMBIC_HEAD: policy_v13.CATALOG_SHA256,
        policy_v14.ALEMBIC_HEAD: policy_v14.CATALOG_SHA256,
    }
    assert policy_v14.CATALOG_SHA256 == "0" * 64
    assert policy_v15.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v14.ALEMBIC_HEAD: policy_v14.CATALOG_SHA256,
        policy_v15.ALEMBIC_HEAD: policy_v15.CATALOG_SHA256,
    }
    assert policy_v15.CATALOG_SHA256 == "0" * 64
    assert policy_v16.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v15.ALEMBIC_HEAD: policy_v15.CATALOG_SHA256,
        policy_v16.ALEMBIC_HEAD: policy_v16.CATALOG_SHA256,
    }
    assert policy_v16.CATALOG_SHA256 == "0" * 64
    assert policy_v17.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v16.ALEMBIC_HEAD: policy_v16.CATALOG_SHA256,
        policy_v17.ALEMBIC_HEAD: policy_v17.CATALOG_SHA256,
    }
    assert policy_v17.CATALOG_SHA256 == "0" * 64
    assert policy_v18.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v17.ALEMBIC_HEAD: policy_v17.CATALOG_SHA256,
        policy_v18.ALEMBIC_HEAD: policy_v18.CATALOG_SHA256,
    }
    assert policy_v18.CATALOG_SHA256 == "0" * 64
    assert policy_v19.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD == {
        policy_v18.ALEMBIC_HEAD: policy_v18.CATALOG_SHA256,
        policy_v19.ALEMBIC_HEAD: policy_v19.CATALOG_SHA256,
    }
    assert policy_v19.CATALOG_SHA256 == "0" * 64


def test_v6_catalog_projection_normalizes_only_postgres_allocation_history() -> None:
    projections = dict(behavior_v6._CATALOG_PROJECTIONS)
    relation = projections["relation"]
    column = projections["column"]

    assert "toast.relname" not in relation
    assert "pg_get_indexdef(ix.indexrelid" not in relation
    assert "toast_ts.spcname" in relation
    assert "toast.reloptions" in relation
    assert column.startswith("SELECT c.relname, a.attname, ")
    assert "ORDER BY c.relname, a.attname" in column
    assert "format_type(a.atttypid, a.atttypmod)" in column
    assert "pg_get_expr(d.adbin, d.adrelid, true)" in column


def test_v4_cutover_counts_unknown_and_reconciliation_legacy_work() -> None:
    class CutoverConnection:
        def __init__(self) -> None:
            self.count_parameters: dict[str, dict[str, str]] = {}

        def scalar(self, statement, parameters=None):
            rendered = str(statement)
            if "has_table_privilege" in rendered:
                return True
            if "FROM public.generation_tasks" in rendered:
                self.count_parameters["generation_tasks"] = dict(parameters)
                return 1
            if "FROM public.relay_submission_outbox" in rendered:
                self.count_parameters["relay_submission_outbox"] = dict(parameters)
                return 2
            raise AssertionError(rendered)

    connection = CutoverConnection()
    assert behavior_v4._legacy_nonterminal_affinity_count(connection) == 3
    assert connection.count_parameters["generation_tasks"] == {
        "legacy_backend_id": "legacy-default-v1",
        "terminal_0": "SUCCEEDED",
        "terminal_1": "FAILED",
        "terminal_2": "CANCELLED",
    }
    assert connection.count_parameters["relay_submission_outbox"] == {
        "legacy_backend_id": "legacy-default-v1",
        "terminal_0": "SENT",
        "terminal_1": "PERMANENTLY_FAILED",
        "terminal_2": "CANCELLED",
    }


def test_v4_cutover_uses_each_process_principals_visible_affinity_tables() -> None:
    class RelaySyncConnection:
        def __init__(self) -> None:
            self.outbox_count_queried = False

        def scalar(self, statement, parameters=None):
            rendered = str(statement)
            if "has_table_privilege" in rendered:
                return "generation_tasks" in rendered
            if "FROM public.generation_tasks" in rendered:
                assert parameters["legacy_backend_id"] == "legacy-default-v1"
                return 1
            if "FROM public.relay_submission_outbox" in rendered:
                self.outbox_count_queried = True
                return 0
            raise AssertionError(rendered)

    connection = RelaySyncConnection()
    assert behavior_v4._legacy_nonterminal_affinity_count(connection) == 1
    assert connection.outbox_count_queried is False


def test_v1_and_v2_catalog_projections_normalize_postgres_generated_oid_names() -> None:
    for behavior in (behavior_v1, behavior_v2):
        projection_by_category = dict(behavior._CATALOG_PROJECTIONS)
        assert "'pg_toast_[0-9]+'" in projection_by_category["relation"]
        assert "'pg_toast_<oid>'" in projection_by_category["relation"]
        assert "AND NOT t.tgisinternal" in projection_by_category["trigger"]


def test_v1_no_longer_accepts_oid_dependent_prequalification_hashes() -> None:
    accepted = {
        policy_v1.CATALOG_SHA256,
        *policy_v1.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.values(),
    }
    assert "9994e7bc49ddf959ae239b41f9a8645d92d4fba63d0b0c6b9eef937e7aa4deba" not in accepted
    assert "84e106377e0aedbff64deac2f23627f9e96184f27d7eb7f58b0d20931de8acde" not in accepted


def test_v2_source_gate_rejects_skipping_0036_role_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ("alembic_version",)
            if "FROM public.alembic_version" in rendered:
                return ("0035_operations_evidence",)
            raise AssertionError(rendered)

    monkeypatch.setattr(
        behavior_v2,
        "platform_catalog_sha256",
        lambda _: policy_v1.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
            "0035_operations_evidence"
        ],
    )
    with pytest.raises(
        behavior_v2.PlatformDatabaseAttestationError,
        match="migration source catalog",
    ):
        behavior_v2.validate_platform_migration_source_state(SourceConnection())


def _v2_pre_migration_evidence(
    *,
    heads: tuple[str, ...],
    default_acl: frozenset[tuple[object, ...]],
) -> SimpleNamespace:
    principals = tuple(
        behavior_v2.DatabasePrincipalEvidence(
            role_name=database_role,
            role_comment=policy_v2.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role],
            can_login=True,
            is_superuser=False,
            inherits=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            bypasses_rls=False,
            connection_limit=(
                policy_v2.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[process_role]
            ),
            credential_validity_ok=True,
        )
        for process_role, database_role in policy_v2.DATABASE_ROLE_BY_PROCESS.items()
    )
    return SimpleNamespace(
        current_user=policy_v2.MIGRATION_DATABASE_ROLE,
        session_user=policy_v2.MIGRATION_DATABASE_ROLE,
        ssl_active=True,
        current_schema="public",
        explicit_schemas=("public",),
        database_owner=policy_v2.MIGRATION_DATABASE_ROLE,
        public_schema_owner="pg_database_owner",
        principals=principals,
        membership_count=0,
        role_setting_count=0,
        parameter_acl_count=0,
        external_owned_object_count=0,
        cross_database_acl_count=0,
        cross_database_dependency_count=0,
        global_role_dependency_count=0,
        system_acl_count=0,
        system_semantic_sha256=(
            POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
        ),
        system_extension_surface_exact=True,
        pgaudit_preloaded=True,
        pgaudit_log_class_coverage=True,
        credential_logging_policy_exact=True,
        system_unsafe_object_count=0,
        public_unsafe_object_count=0,
        legacy_pending_work_count=0,
        foreign_owned_object_count=0,
        column_acl_count=0,
        alembic_heads=heads,
        default_acl=default_acl,
    )


def test_v2_predecessor_accepts_only_exact_0036_default_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _v2_pre_migration_evidence(
        heads=(policy_v1.ALEMBIC_HEAD,),
        default_acl=policy_v2.EXPECTED_DEFAULT_ACL,
    )
    behavior_v2.validate_platform_database_evidence(
        evidence,
        "migration",
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v2,
    )


@pytest.mark.parametrize(
    "default_acl",
    (
        pytest.param(frozenset(), id="empty"),
        pytest.param(
            policy_v2.EXPECTED_DEFAULT_ACL
            | {
                (
                    policy_v2.MIGRATION_DATABASE_ROLE,
                    "",
                    "TABLE",
                    "PUBLIC",
                    "SELECT",
                    policy_v2.MIGRATION_DATABASE_ROLE,
                    False,
                )
            },
            id="extra",
        ),
        pytest.param(
            {
                (
                    policy_v2.MIGRATION_DATABASE_ROLE,
                    "",
                    "FUNCTION",
                    "PUBLIC",
                    "EXECUTE",
                    policy_v2.MIGRATION_DATABASE_ROLE,
                    False,
                )
            },
            id="missing-exact-entry",
        ),
    ),
)
def test_v2_predecessor_rejects_default_acl_drift(
    monkeypatch: pytest.MonkeyPatch,
    default_acl: frozenset[tuple[object, ...]],
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _v2_pre_migration_evidence(
        heads=(policy_v1.ALEMBIC_HEAD,),
        default_acl=default_acl,
    )
    with pytest.raises(
        behavior_v2.PlatformDatabaseAttestationError,
        match="pre-migration default privileges",
    ):
        behavior_v2.validate_platform_database_evidence(
            evidence,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v2,
        )


@pytest.mark.parametrize(
    "heads",
    (("unknown_head",), (policy_v1.ALEMBIC_HEAD, "unknown_head")),
)
def test_v2_pre_migration_rejects_unknown_and_multi_heads(
    monkeypatch: pytest.MonkeyPatch,
    heads: tuple[str, ...],
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _v2_pre_migration_evidence(
        heads=heads,
        default_acl=policy_v2.EXPECTED_DEFAULT_ACL,
    )
    with pytest.raises(
        behavior_v2.PlatformDatabaseAttestationError,
        match="pre-migration head",
    ):
        behavior_v2.validate_platform_database_evidence(
            evidence,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v2,
        )


def test_v2_empty_source_requires_empty_default_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    empty = _v2_pre_migration_evidence(heads=(), default_acl=frozenset())
    behavior_v2.validate_platform_database_evidence(
        empty,
        "migration",
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v2,
    )
    with pytest.raises(
        behavior_v2.PlatformDatabaseAttestationError,
        match="pre-migration default privileges",
    ):
        behavior_v2.validate_platform_database_evidence(
            _v2_pre_migration_evidence(
                heads=(),
                default_acl=policy_v2.EXPECTED_DEFAULT_ACL,
            ),
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v2,
        )


def test_v2_current_head_still_uses_full_acl_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    calls: list[tuple[bool, object]] = []
    monkeypatch.setattr(
        behavior_v2,
        "validate_platform_database_acl_evidence",
        lambda _evidence, *, require_head, policy: calls.append(
            (require_head, policy)
        ),
    )
    behavior_v2.validate_platform_database_evidence(
        _v2_pre_migration_evidence(
            heads=(policy_v2.ALEMBIC_HEAD,),
            default_acl=policy_v2.EXPECTED_DEFAULT_ACL,
        ),
        "migration",
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v2,
    )
    assert calls == [(True, policy_v2)]


def test_live_source_gate_routes_only_exact_0035_through_frozen_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ("alembic_version", "users")
            if "FROM public.alembic_version" in rendered:
                return ("0035_operations_evidence",)
            raise AssertionError(rendered)

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        behavior_v1,
        "validate_platform_migration_source_state",
        lambda connection, *, policy: calls.append(("v1", policy)),
    )
    monkeypatch.setattr(
        behavior_v2,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("0035 source reached v2"),
    )
    monkeypatch.setattr(
        behavior_v3,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("0035 source reached v3"),
    )

    privileges.validate_platform_migration_source_state(SourceConnection())

    assert calls == [("v1", policy_v1)]


def test_live_source_gate_routes_exact_0036_through_frozen_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ("alembic_version", "users")
            if "FROM public.alembic_version" in rendered:
                return (policy_v1.ALEMBIC_HEAD,)
            raise AssertionError(rendered)

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        behavior_v1,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("0036 source reached v1"),
    )
    monkeypatch.setattr(
        behavior_v2,
        "validate_platform_migration_source_state",
        lambda connection, *, policy: calls.append(("v2", policy)),
    )
    monkeypatch.setattr(
        behavior_v3,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("0036 source reached v3"),
    )

    privileges.validate_platform_migration_source_state(SourceConnection())

    assert calls == [("v2", policy_v2)]


def test_live_source_gate_routes_truly_empty_database_through_current_v18(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ()
            raise AssertionError(rendered)

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        behavior_v1,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v1"),
    )
    monkeypatch.setattr(
        behavior_v2,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v2"),
    )
    monkeypatch.setattr(
        behavior_v3,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v3"),
    )
    monkeypatch.setattr(
        behavior_v4,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v4"),
    )
    monkeypatch.setattr(
        behavior_v5,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v5"),
    )
    monkeypatch.setattr(
        behavior_v6,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v6"),
    )
    monkeypatch.setattr(
        behavior_v7,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v7"),
    )
    monkeypatch.setattr(
        behavior_v8,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v8"),
    )
    monkeypatch.setattr(
        behavior_v9,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v9"),
    )
    monkeypatch.setattr(
        behavior_v10,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v10"),
    )
    monkeypatch.setattr(
        behavior_v11,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v11"),
    )
    monkeypatch.setattr(
        behavior_v12,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v12"),
    )
    monkeypatch.setattr(
        behavior_v13,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v13"),
    )
    monkeypatch.setattr(
        behavior_v14,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v14"),
    )
    monkeypatch.setattr(
        behavior_v15,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v15"),
    )
    monkeypatch.setattr(
        behavior_v16,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v16"),
    )
    monkeypatch.setattr(
        behavior_v17,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("empty source reached v17"),
    )
    monkeypatch.setattr(
        behavior_v18,
        "validate_platform_migration_source_state",
        lambda connection, *, policy: calls.append(("v18", policy)),
    )

    privileges.validate_platform_migration_source_state(EmptyConnection())

    assert calls == [("v18", policy_v18)]


@pytest.mark.parametrize(
    "heads",
    [
        (),
        (policy_v17.ALEMBIC_HEAD,),
        (policy_v18.ALEMBIC_HEAD,),
        ("0035_operations_evidence", policy_v1.ALEMBIC_HEAD),
        ("unknown_head",),
    ],
)
def test_live_source_gate_keeps_current_and_unknown_sources_on_v18(
    monkeypatch: pytest.MonkeyPatch,
    heads: tuple[str, ...],
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ("alembic_version", "users")
            if "FROM public.alembic_version" in rendered:
                return heads
            raise AssertionError(rendered)

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        behavior_v1,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v1"),
    )
    monkeypatch.setattr(
        behavior_v2,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v2"),
    )
    monkeypatch.setattr(
        behavior_v3,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v3"),
    )
    monkeypatch.setattr(
        behavior_v4,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v4"),
    )
    monkeypatch.setattr(
        behavior_v5,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v5"),
    )
    monkeypatch.setattr(
        behavior_v6,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v6"),
    )
    monkeypatch.setattr(
        behavior_v7,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v7"),
    )
    monkeypatch.setattr(
        behavior_v8,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v8"),
    )
    monkeypatch.setattr(
        behavior_v9,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v9"),
    )
    monkeypatch.setattr(
        behavior_v10,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v10"),
    )
    monkeypatch.setattr(
        behavior_v11,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v11"),
    )
    monkeypatch.setattr(
        behavior_v12,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v12"),
    )
    monkeypatch.setattr(
        behavior_v13,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v13"),
    )
    monkeypatch.setattr(
        behavior_v14,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v14"),
    )
    monkeypatch.setattr(
        behavior_v15,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v15"),
    )
    monkeypatch.setattr(
        behavior_v16,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v16"),
    )
    monkeypatch.setattr(
        behavior_v17,
        "validate_platform_migration_source_state",
        lambda *_args, **_kwargs: pytest.fail("current source reached v17"),
    )
    monkeypatch.setattr(
        behavior_v18,
        "validate_platform_migration_source_state",
        lambda connection, *, policy: calls.append(("v18", policy)),
    )

    privileges.validate_platform_migration_source_state(SourceConnection())

    assert calls == [("v18", policy_v18)]


def test_v6_validates_qualified_0040_source_with_frozen_v5_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True)
    class SourceEvidence:
        catalog_sha256: str
        alembic_heads: tuple[str, ...]

    source_digest = policy_v6.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
        policy_v5.ALEMBIC_HEAD
    ]
    evidence = SourceEvidence(
        catalog_sha256=source_digest,
        alembic_heads=(policy_v5.ALEMBIC_HEAD,),
    )
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        behavior_v5,
        "validate_platform_database_evidence",
        lambda actual, process_role, *, require_runtime_acl, require_head,
        policy: calls.append(
            (
                actual,
                process_role,
                require_runtime_acl,
                require_head,
                policy,
            )
        ),
    )

    behavior_v6.validate_platform_database_evidence(
        evidence,
        "migration",
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v6,
    )

    assert len(calls) == 1
    normalized, process_role, runtime_acl, require_head, policy = calls[0]
    assert normalized.catalog_sha256 == policy_v5.CATALOG_SHA256
    assert normalized.alembic_heads == (policy_v5.ALEMBIC_HEAD,)
    assert process_role == "migration"
    assert runtime_acl is False
    assert require_head is False
    assert policy is policy_v5


def test_v6_current_real_evidence_normalizes_its_new_table_for_v5() -> None:
    principals = tuple(
        behavior_v6.DatabasePrincipalEvidence(
            role_name=database_role,
            role_comment=policy_v6.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role],
            can_login=True,
            is_superuser=False,
            inherits=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            bypasses_rls=False,
            connection_limit=(
                policy_v6.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[process_role]
            ),
            credential_validity_ok=True,
        )
        for process_role, database_role in policy_v6.DATABASE_ROLE_BY_PROCESS.items()
    )
    evidence = behavior_v6.PlatformDatabaseEvidence(
        current_user=policy_v6.MIGRATION_DATABASE_ROLE,
        session_user=policy_v6.MIGRATION_DATABASE_ROLE,
        ssl_active=True,
        current_schema="public",
        explicit_schemas=("public",),
        database_owner=policy_v6.MIGRATION_DATABASE_ROLE,
        public_schema_owner="pg_database_owner",
        principals=principals,
        membership_count=0,
        role_setting_count=0,
        parameter_acl_count=0,
        external_owned_object_count=0,
        cross_database_acl_count=0,
        cross_database_dependency_count=0,
        global_role_dependency_count=0,
        system_acl_count=0,
        system_acl_sha256=policy_v6.SYSTEM_ACL_SHA256,
        system_semantic_sha256=(
            POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
        ),
        system_extension_surface_exact=True,
        pgaudit_preloaded=True,
        pgaudit_log_class_coverage=True,
        credential_logging_policy_exact=True,
        system_unsafe_object_count=0,
        public_unsafe_object_count=0,
        legacy_pending_work_count=0,
        foreign_owned_object_count=0,
        column_acl_count=0,
        database_acl=frozenset(
            (
                role,
                privilege,
                policy_v6.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v6.EXPECTED_DATABASE_ACL
        ),
        schema_acl=frozenset(
            (role, privilege, "pg_database_owner", False)
            for role, privilege in policy_v6.EXPECTED_SCHEMA_ACL
        ),
        table_names=policy_v6.TABLES | {"alembic_version"},
        table_acl=frozenset(
            (
                table_name,
                role,
                privilege,
                policy_v6.MIGRATION_DATABASE_ROLE,
                False,
            )
            for table_name, role, privilege in policy_v6.EXPECTED_TABLE_ACL
        ),
        sequence_acl=frozenset(),
        routine_acl=frozenset(),
        default_acl=policy_v6.EXPECTED_DEFAULT_ACL,
        catalog_sha256=policy_v6.CATALOG_SHA256,
        alembic_heads=(policy_v6.ALEMBIC_HEAD,),
    )

    behavior_v6.validate_platform_database_evidence(
        evidence,
        "migration",
        require_runtime_acl=False,
        require_head=True,
        policy=policy_v6,
    )


def test_live_source_gate_normalizes_frozen_v1_failure_to_facade_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ("alembic_version", "users")
            if "FROM public.alembic_version" in rendered:
                return ("0035_operations_evidence",)
            raise AssertionError(rendered)

    def reject(*_args, **_kwargs):
        raise behavior_v1.PlatformDatabaseAttestationError(
            "protected Platform database attestation failed: migration source catalog"
        )

    monkeypatch.setattr(
        behavior_v1,
        "validate_platform_migration_source_state",
        reject,
    )
    with pytest.raises(
        privileges.PlatformDatabaseAttestationError,
        match="migration source catalog",
    ):
        privileges.validate_platform_migration_source_state(SourceConnection())


@pytest.mark.parametrize(
    ("heads", "has_version", "expected_policy", "expected_behavior", "is_current"),
    [
        ((), False, policy_v29, behavior_v29, False),
        (("0035_operations_evidence",), True, policy_v1, behavior_v1, False),
        ((policy_v1.ALEMBIC_HEAD,), True, policy_v2, behavior_v2, False),
        ((policy_v2.ALEMBIC_HEAD,), True, policy_v3, behavior_v3, False),
        ((policy_v3.ALEMBIC_HEAD,), True, policy_v4, behavior_v4, False),
        ((policy_v4.ALEMBIC_HEAD,), True, policy_v5, behavior_v5, False),
        ((policy_v5.ALEMBIC_HEAD,), True, policy_v6, behavior_v6, False),
        ((policy_v6.ALEMBIC_HEAD,), True, policy_v7, behavior_v7, False),
        ((policy_v7.ALEMBIC_HEAD,), True, policy_v8, behavior_v8, False),
        ((policy_v8.ALEMBIC_HEAD,), True, policy_v9, behavior_v9, False),
        ((policy_v9.ALEMBIC_HEAD,), True, policy_v10, behavior_v10, False),
        ((policy_v10.ALEMBIC_HEAD,), True, policy_v11, behavior_v11, False),
        ((policy_v11.ALEMBIC_HEAD,), True, policy_v12, behavior_v12, False),
        ((policy_v12.ALEMBIC_HEAD,), True, policy_v13, behavior_v13, False),
        ((policy_v13.ALEMBIC_HEAD,), True, policy_v14, behavior_v14, False),
        ((policy_v14.ALEMBIC_HEAD,), True, policy_v15, behavior_v15, False),
        ((policy_v15.ALEMBIC_HEAD,), True, policy_v16, behavior_v16, False),
        ((policy_v16.ALEMBIC_HEAD,), True, policy_v17, behavior_v17, False),
        ((policy_v17.ALEMBIC_HEAD,), True, policy_v18, behavior_v18, False),
        ((policy_v18.ALEMBIC_HEAD,), True, policy_v19, behavior_v19, False),
        ((policy_v19.ALEMBIC_HEAD,), True, policy_v20, behavior_v20, False),
        ((policy_v20.ALEMBIC_HEAD,), True, policy_v21, behavior_v21, False),
        ((policy_v21.ALEMBIC_HEAD,), True, policy_v22, behavior_v22, False),
        ((policy_v22.ALEMBIC_HEAD,), True, policy_v23, behavior_v23, False),
        ((policy_v23.ALEMBIC_HEAD,), True, policy_v24, behavior_v24, False),
        ((policy_v24.ALEMBIC_HEAD,), True, policy_v25, behavior_v25, False),
        ((policy_v25.ALEMBIC_HEAD,), True, policy_v26, behavior_v26, False),
        ((policy_v26.ALEMBIC_HEAD,), True, policy_v27, behavior_v27, False),
        ((policy_v27.ALEMBIC_HEAD,), True, policy_v28, behavior_v28, False),
        ((policy_v28.ALEMBIC_HEAD,), True, policy_v29, behavior_v29, False),
        ((policy_v29.ALEMBIC_HEAD,), True, policy_v29, behavior_v29, True),
    ],
    ids=(
        "empty",
        "0035",
        "0036",
        "0037",
        "0038",
        "0039",
        "0040",
        "0041",
        "0042",
        "0043",
        "0044",
        "0045",
        "0046",
        "0047",
        "0048",
        "0049",
        "0050",
        "0051",
        "0052",
        "0053",
        "0054",
        "0055",
        "0056",
        "0057",
        "0058",
        "0059",
        "0060",
        "0061",
        "0062",
        "0063",
        "0064-current",
    ),
)
def test_migration_only_evidence_helper_uses_the_frozen_source_policy(
    monkeypatch: pytest.MonkeyPatch,
    heads: tuple[str, ...],
    has_version: bool,
    expected_policy: object,
    expected_behavior: object,
    is_current: bool,
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return ("alembic_version", "users") if has_version else ()
            if "FROM public.alembic_version" in rendered:
                return heads
            raise AssertionError(rendered)

    calls: list[tuple[object, ...]] = []
    evidence = SimpleNamespace(alembic_heads=heads)
    for behavior in (
        behavior_v1,
        behavior_v2,
        behavior_v3,
        behavior_v4,
        behavior_v5,
        behavior_v6,
        behavior_v7,
        behavior_v8,
        behavior_v9,
        behavior_v10,
        behavior_v11,
        behavior_v12,
        behavior_v13,
        behavior_v14,
        behavior_v15,
        behavior_v16,
        behavior_v17,
        behavior_v18,
        behavior_v19,
        behavior_v20,
        behavior_v21,
        behavior_v22,
        behavior_v23,
        behavior_v24,
        behavior_v25,
        behavior_v26,
        behavior_v27,
        behavior_v28,
        behavior_v29,
    ):
        monkeypatch.setattr(
            behavior,
            "validate_platform_migration_source_state",
            lambda _connection, *, policy, selected=behavior: calls.append(
                ("source", selected, policy)
            ),
        )
        monkeypatch.setattr(
            behavior,
            "collect_platform_database_evidence",
            lambda _connection, *, policy, selected=behavior: (
                calls.append(("collect", selected, policy)) or evidence
            ),
        )
        monkeypatch.setattr(
            behavior,
            "validate_platform_database_evidence",
            lambda actual, process_role, *, require_runtime_acl, require_head,
            policy, selected=behavior: calls.append(
                (
                    "evidence",
                    selected,
                    policy,
                    actual,
                    process_role,
                    require_runtime_acl,
                    require_head,
                )
            ),
        )

    actual = privileges.validate_platform_migration_database_evidence(
        SourceConnection()
    )

    assert actual is evidence
    assert calls == [
        ("source", expected_behavior, expected_policy),
        ("collect", expected_behavior, expected_policy),
        (
            "evidence",
            expected_behavior,
            expected_policy,
            evidence,
            "migration",
            False,
            is_current,
        ),
    ]


@pytest.mark.parametrize(
    ("table_names", "heads"),
    [
        (("alembic_version", "users"), ("unknown_head",)),
        (
            ("alembic_version", "users"),
            (policy_v2.ALEMBIC_HEAD, policy_v3.ALEMBIC_HEAD),
        ),
        (("users",), ()),
    ],
    ids=("unknown", "multi-head", "dirty-shape"),
)
def test_migration_only_evidence_helper_rejects_unqualified_v14_sources(
    monkeypatch: pytest.MonkeyPatch,
    table_names: tuple[str, ...],
    heads: tuple[str, ...],
) -> None:
    class SourceConnection:
        def scalars(self, statement):
            rendered = str(statement)
            if "FROM pg_class" in rendered:
                return table_names
            if "FROM public.alembic_version" in rendered:
                return heads
            raise AssertionError(rendered)

    monkeypatch.setattr(
        behavior_v14,
        "platform_catalog_sha256",
        lambda _connection: policy_v14.EMPTY_CATALOG_SHA256,
    )
    monkeypatch.setattr(
        behavior_v14,
        "collect_platform_database_evidence",
        lambda *_args, **_kwargs: pytest.fail("rejected source was collected"),
    )
    with pytest.raises(
        privileges.PlatformDatabaseAttestationError,
        match="migration source (head|catalog)",
    ):
        privileges.validate_platform_migration_database_evidence(
            SourceConnection()
        )


def test_protected_alembic_orders_source_evidence_proof_before_any_ddl() -> None:
    platform_root = Path(__file__).parents[1]
    source = (platform_root / "migrations" / "env.py").read_text(
        encoding="utf-8"
    )
    online = source.split("def run_migrations_online() -> None:", 1)[1]
    source_gate = online.index("validate_platform_migration_source_state(connection)")
    evidence_gate = online.index(
        "validate_platform_migration_database_evidence("
    )
    proof_gate = online.index(
        "attest_platform_database_release_proof(connection, source_evidence)"
    )
    rollback = online.index("connection.rollback()")
    configure = online.index("        context.configure(")
    ddl = online.index("            context.run_migrations()")
    post_ddl = online.index("validate_platform_database_acl_evidence(")
    assert source_gate < evidence_gate < proof_gate < rollback < configure < ddl
    assert ddl < post_ddl
    assert "attest_platform_database_connection(" not in online

    role_pre = (platform_root / "platform_api" / "database_role_pre.py").read_text(
        encoding="utf-8"
    )
    login_gate = role_pre.split("def _verify_role_logins", 1)[1].split(
        "def _publish_platform_database_release_proof", 1
    )[0]
    assert login_gate.index(
        "validate_platform_migration_source_state(connection)"
    ) < login_gate.index(
        "validate_platform_migration_database_evidence(connection)"
    )


def test_v2_table_manifest_exactly_matches_current_metadata() -> None:
    assert policy_v2.TABLES == policy_v1.TABLES | AUTH_TABLES
    assert policy_v3.TABLES == policy_v2.TABLES
    assert policy_v4.TABLES == policy_v3.TABLES
    assert policy_v5.TABLES == policy_v4.TABLES | {
        "showcase_channels",
        "showcase_draft_items",
        "showcase_media",
        "showcase_publication_events",
        "showcase_release_items",
        "showcase_releases",
    }
    assert policy_v6.TABLES == policy_v5.TABLES | {
        "personal_model_grant_batch_journals"
    }
    assert policy_v7.TABLES == policy_v6.TABLES | {
        "company_entitlement_batch_journals"
    }
    assert policy_v8.TABLES == policy_v7.TABLES
    assert policy_v8.PRIVILEGES_BY_PROCESS == policy_v7.PRIVILEGES_BY_PROCESS
    assert policy_v9.TABLES == policy_v8.TABLES
    assert policy_v9.PRIVILEGES_BY_PROCESS == policy_v8.PRIVILEGES_BY_PROCESS
    assert policy_v10.TABLES == policy_v9.TABLES
    assert policy_v10.PRIVILEGES_BY_PROCESS["relay-catalog-sync"] == {
        "model_definitions": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "model_capabilities": frozenset({"SELECT", "INSERT"}),
        "audit_logs": frozenset({"INSERT"}),
    }
    assert "users" not in policy_v10.PRIVILEGES_BY_PROCESS[
        "relay-catalog-sync"
    ]
    assert policy_v10.DATABASE_ROLE_BY_PROCESS["relay-catalog-sync"] == (
        "platform_relay_catalog_sync"
    )
    assert policy_v10.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[
        "relay-catalog-sync"
    ] == 4
    assert policy_v11.TABLES == policy_v10.TABLES | {
        "company_point_wallet_accounts",
        "company_point_lots",
        "company_point_ledger_entries",
        "task_point_lot_allocations",
        "company_point_price_versions",
    }
    assert policy_v12.TABLES == policy_v11.TABLES | {
        "auth_product_context_switches"
    }
    assert policy_v12.PRIVILEGES_BY_PROCESS["platform-api"][
        "auth_product_context_switches"
    ] == frozenset({"SELECT", "INSERT"})
    for process_role, table_privileges in policy_v12.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert "auth_product_context_switches" not in table_privileges
    assert policy_v7.PRIVILEGES_BY_PROCESS["platform-api"][
        "company_entitlement_batch_journals"
    ] == frozenset({"SELECT", "INSERT", "UPDATE"})
    for process_role, table_privileges in policy_v7.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert "company_entitlement_batch_journals" not in table_privileges
    assert policy_v13.TABLES == policy_v12.TABLES | policy_v13.COMMERCIAL_TABLES
    assert policy_v13.APPEND_ONLY_COMMERCIAL_TABLES.isdisjoint(
        policy_v13.MUTABLE_COMMERCIAL_TABLES
    )
    for table_name in policy_v13.APPEND_ONLY_COMMERCIAL_TABLES:
        assert policy_v13.PRIVILEGES_BY_PROCESS["platform-api"][table_name] == (
            frozenset({"SELECT", "INSERT"})
        )
    for table_name in policy_v13.MUTABLE_COMMERCIAL_TABLES:
        assert policy_v13.PRIVILEGES_BY_PROCESS["platform-api"][table_name] == (
            frozenset({"SELECT", "INSERT", "UPDATE"})
        )
    worker_projection_tables = {
        "personal_point_lots",
        "personal_task_point_lot_allocations",
        "point_lot_settlement_value_allocations",
    }
    for process_role, table_privileges in policy_v13.PRIVILEGES_BY_PROCESS.items():
        if process_role == "platform-api":
            continue
        if process_role == "dispatcher":
            assert policy_v13.COMMERCIAL_TABLES & set(table_privileges) == {
                "personal_point_lots",
                "personal_task_point_lot_allocations",
            }
        elif process_role in {"relay-sync", "timeout-worker"}:
            assert policy_v13.COMMERCIAL_TABLES & set(table_privileges) == (
                worker_projection_tables
            )
        else:
            assert policy_v13.COMMERCIAL_TABLES.isdisjoint(table_privileges)
    assert policy_v14.TABLES == policy_v13.TABLES | policy_v14.CLOSURE_TABLES
    assert policy_v14.APPEND_ONLY_CLOSURE_TABLES.isdisjoint(
        policy_v14.MUTABLE_CLOSURE_TABLES
    )
    for table_name in policy_v14.APPEND_ONLY_CLOSURE_TABLES:
        assert policy_v14.PRIVILEGES_BY_PROCESS["platform-api"][table_name] == (
            frozenset({"SELECT", "INSERT"})
        )
    for table_name in policy_v14.MUTABLE_CLOSURE_TABLES:
        assert policy_v14.PRIVILEGES_BY_PROCESS["platform-api"][table_name] == (
            frozenset({"SELECT", "INSERT", "UPDATE"})
        )
    for process_role, table_privileges in policy_v14.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert policy_v14.CLOSURE_TABLES.isdisjoint(table_privileges)
    assert policy_v15.TABLES == (
        policy_v14.TABLES | policy_v15.MODEL_COMMERCIAL_RELEASE_TABLES
    )
    assert policy_v15.PRIVILEGES_BY_PROCESS["platform-api"][
        "model_commercial_release_plans"
    ] == frozenset({"SELECT", "INSERT"})
    assert policy_v15.PRIVILEGES_BY_PROCESS["relay-catalog-sync"][
        "model_commercial_release_plans"
    ] == frozenset({"SELECT"})
    assert policy_v15.PRIVILEGES_BY_PROCESS["relay-catalog-sync"][
        "model_commercial_release_executions"
    ] == frozenset({"SELECT", "UPDATE"})
    assert policy_v16.TABLES == policy_v15.TABLES
    assert policy_v16.PRIVILEGES_BY_PROCESS == policy_v15.PRIVILEGES_BY_PROCESS
    assert policy_v17.TABLES == policy_v16.TABLES
    assert policy_v17.PRIVILEGES_BY_PROCESS == policy_v16.PRIVILEGES_BY_PROCESS
    assert policy_v18.TABLES == (
        policy_v17.TABLES | policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES
    )
    assert policy_v18.PRIVILEGES_BY_PROCESS["platform-api"][
        "director_shot_packages"
    ] == frozenset({"SELECT", "INSERT"})
    assert policy_v18.PRIVILEGES_BY_PROCESS["platform-api"][
        "task_director_shot_packages"
    ] == frozenset({"SELECT", "INSERT"})
    for process_role, table_privileges in policy_v18.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES.isdisjoint(
                table_privileges
            )
    def _metadata_as_of(policy: object) -> dict[str, object]:
        """Base metadata restricted to the tables one frozen policy declared.

        ``assert_platform_database_manifest_matches_metadata`` compares for
        equality, so once a later revision adds a table every historical
        assertion would fail.  Restricting to that policy's own manifest keeps
        the meaningful invariant: no table a frozen policy declared has since
        disappeared from the metadata.  The current policy is still checked
        against the full metadata below.
        """

        return {
            name: table
            for name, table in Base.metadata.tables.items()
            if name in policy.TABLES
        }

    behavior_v18.assert_platform_database_manifest_matches_metadata(
        _metadata_as_of(policy_v18)
    )
    assert policy_v19.TABLES == policy_v18.TABLES
    assert policy_v19.PRIVILEGES_BY_PROCESS == policy_v18.PRIVILEGES_BY_PROCESS
    behavior_v19.assert_platform_database_manifest_matches_metadata(
        _metadata_as_of(policy_v19)
    )
    for predecessor, current in (
        (policy_v19, policy_v20),
        (policy_v20, policy_v21),
        (policy_v21, policy_v22),
        (policy_v22, policy_v23),
        (policy_v23, policy_v24),
    ):
        assert current.TABLES == predecessor.TABLES
        assert current.PRIVILEGES_BY_PROCESS == predecessor.PRIVILEGES_BY_PROCESS
        assert current.CATALOG_SHA256 == current.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    behavior_v24.assert_platform_database_manifest_matches_metadata(
        _metadata_as_of(policy_v24)
    )

    # Every revision in the loop above keeps the v19 table and privilege
    # manifests exactly.  Revision 0060 is the first successor in that range to
    # widen them, so it asserts the exact expected delta instead of equality.
    assert policy_v25.TABLES == policy_v24.TABLES | {
        "model_commercial_release_batches"
    }
    assert policy_v25.CATALOG_SHA256 == policy_v25.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    expected_privileges = {
        process: dict(table_privileges)
        for process, table_privileges in policy_v24.PRIVILEGES_BY_PROCESS.items()
    }
    expected_privileges["platform-api"]["model_commercial_release_batches"] = (
        frozenset({"SELECT", "INSERT", "UPDATE"})
    )
    assert {
        process: dict(table_privileges)
        for process, table_privileges in policy_v25.PRIVILEGES_BY_PROCESS.items()
    } == expected_privileges
    for process_role, table_privileges in policy_v25.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert "model_commercial_release_batches" not in table_privileges
    behavior_v25.assert_platform_database_manifest_matches_metadata(
        _metadata_as_of(policy_v25)
    )

    # 0061 opens one transition in the released execution guard. It adds no
    # table and widens no privilege, so the v25 manifests carry over exactly
    # and the current policy still matches the whole metadata.
    assert policy_v26.TABLES == policy_v25.TABLES
    assert policy_v26.PRIVILEGES_BY_PROCESS == policy_v25.PRIVILEGES_BY_PROCESS
    assert (
        policy_v26.CATALOG_SHA256
        == policy_v26.UNQUALIFIED_CATALOG_SHA256
        == "0" * 64
    )
    behavior_v26.assert_platform_database_manifest_matches_metadata(
        _metadata_as_of(policy_v26)
    )

    # 0062 adds share_links, the anonymous read-only handle table. Only
    # platform-api touches it, and deliberately without DELETE: a share
    # link is revoked, never erased.
    assert policy_v27.TABLES == policy_v26.TABLES | {
        "share_links"
    }
    assert (
        policy_v27.CATALOG_SHA256
        == policy_v27.UNQUALIFIED_CATALOG_SHA256
        == "0" * 64
    )
    expected_privileges_27 = {
        process: dict(table_privileges)
        for process, table_privileges in policy_v26.PRIVILEGES_BY_PROCESS.items()
    }
    expected_privileges_27["platform-api"]["share_links"] = frozenset(
        {"SELECT", "INSERT", "UPDATE"}
    )
    assert {
        process: dict(table_privileges)
        for process, table_privileges in policy_v27.PRIVILEGES_BY_PROCESS.items()
    } == expected_privileges_27
    for process_role, table_privileges in policy_v27.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert "share_links" not in table_privileges
    behavior_v27.assert_platform_database_manifest_matches_metadata(
        _metadata_as_of(policy_v27)
    )


def test_auth_tables_are_owned_only_by_platform_api_runtime_role() -> None:
    api = policy_v2.PRIVILEGES_BY_PROCESS["platform-api"]
    assert {
        table_name: api[table_name]
        for table_name in AUTH_TABLES | {"users"}
    } == {
        "account_security_events": frozenset({"SELECT", "INSERT"}),
        "auth_sessions": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "company_invitations": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "external_identities": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "oidc_login_transactions": frozenset(
            {"SELECT", "INSERT", "UPDATE", "DELETE"}
        ),
        "users": frozenset({"SELECT", "INSERT", "UPDATE"}),
    }
    for process_role, table_privileges in policy_v2.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert AUTH_TABLES.isdisjoint(table_privileges)


def test_v2_keeps_existing_principal_identity_contract() -> None:
    assert policy_v2.DATABASE_ROLE_BY_PROCESS == policy_v1.DATABASE_ROLE_BY_PROCESS
    assert (
        policy_v2.DATABASE_ROLE_COMMENT_BY_PROCESS
        == policy_v1.DATABASE_ROLE_COMMENT_BY_PROCESS
    )
    assert (
        policy_v2.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
        == policy_v1.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
    )
