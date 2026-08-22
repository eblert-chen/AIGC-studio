from __future__ import annotations

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
from platform_api import database_privileges_v1 as policy_v1
from platform_api import database_privileges_v2 as policy_v2
from platform_api import database_privileges_v3 as policy_v3
from platform_api import database_privileges_v4 as policy_v4
from platform_api import database_privileges_v5 as policy_v5
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


def test_runtime_facade_selects_v5_and_keeps_frozen_registry_entries() -> None:
    assert privileges.CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY is policy_v5
    assert privileges.CURRENT_PLATFORM_DATABASE_PRIVILEGE_BEHAVIOR is behavior_v5
    assert privileges.PLATFORM_ALEMBIC_HEAD == policy_v5.ALEMBIC_HEAD
    assert privileges.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY == {
        policy_v1.ALEMBIC_HEAD: (policy_v1, behavior_v1),
        policy_v2.ALEMBIC_HEAD: (policy_v2, behavior_v2),
        policy_v3.ALEMBIC_HEAD: (policy_v3, behavior_v3),
        policy_v4.ALEMBIC_HEAD: (policy_v4, behavior_v4),
        policy_v5.ALEMBIC_HEAD: (policy_v5, behavior_v5),
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


def test_live_source_gate_routes_truly_empty_database_through_current_v5(
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
        lambda connection, *, policy: calls.append(("v5", policy)),
    )

    privileges.validate_platform_migration_source_state(EmptyConnection())

    assert calls == [("v5", policy_v5)]


@pytest.mark.parametrize(
    "heads",
    [
        (),
        (policy_v4.ALEMBIC_HEAD,),
        ("0035_operations_evidence", policy_v1.ALEMBIC_HEAD),
        ("unknown_head",),
    ],
)
def test_live_source_gate_keeps_current_and_unknown_sources_on_v5(
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
        lambda connection, *, policy: calls.append(("v5", policy)),
    )

    privileges.validate_platform_migration_source_state(SourceConnection())

    assert calls == [("v5", policy_v5)]


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
        ((), False, policy_v5, behavior_v5, False),
        (("0035_operations_evidence",), True, policy_v1, behavior_v1, False),
        ((policy_v1.ALEMBIC_HEAD,), True, policy_v2, behavior_v2, False),
        ((policy_v2.ALEMBIC_HEAD,), True, policy_v3, behavior_v3, False),
        ((policy_v3.ALEMBIC_HEAD,), True, policy_v4, behavior_v4, False),
        ((policy_v4.ALEMBIC_HEAD,), True, policy_v5, behavior_v5, False),
        ((policy_v5.ALEMBIC_HEAD,), True, policy_v5, behavior_v5, True),
    ],
    ids=("empty", "0035", "0036", "0037", "0038", "0039", "0040-current"),
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
    for behavior in (behavior_v1, behavior_v2, behavior_v3, behavior_v4, behavior_v5):
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
def test_migration_only_evidence_helper_rejects_unqualified_v5_sources(
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
        behavior_v5,
        "platform_catalog_sha256",
        lambda _connection: policy_v5.EMPTY_CATALOG_SHA256,
    )
    monkeypatch.setattr(
        behavior_v5,
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
    behavior_v5.assert_platform_database_manifest_matches_metadata(
        Base.metadata.tables
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
