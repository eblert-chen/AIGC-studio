from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from platform_api.models import ModelDefinition
from platform_api.services.models import ModelCatalogService
from platform_api.services.relay_capabilities import RelayCapabilityService


PREVIOUS_HEAD = "0040_showcase_management"
CURRENT_HEAD = "0041_model_capability_releases"
CAPABILITY_RELEASE_COLUMNS = {
    "relay_capability_candidate_revision",
    "relay_capability_candidate_catalog_revision",
    "relay_capability_candidate",
    "relay_capability_candidate_synced_at",
    "relay_capability_approved_ceiling",
    "relay_capability_approved_catalog_revision",
}
PERSONAL_BATCH_JOURNAL = "personal_model_grant_batch_journals"


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _head(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _columns(engine) -> set[str]:
    return {
        str(column["name"])
        for column in inspect(engine).get_columns("model_definitions")
    }


def test_capability_release_columns_round_trip_at_single_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "capability-release.db"
    config = _config(project_root, database_path)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == PREVIOUS_HEAD
    assert CAPABILITY_RELEASE_COLUMNS.isdisjoint(_columns(engine))
    assert PERSONAL_BATCH_JOURNAL not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, CURRENT_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == CURRENT_HEAD
    assert CAPABILITY_RELEASE_COLUMNS <= _columns(engine)
    inspector = inspect(engine)
    assert PERSONAL_BATCH_JOURNAL in inspector.get_table_names()
    journal_columns = {
        str(column["name"])
        for column in inspector.get_columns(PERSONAL_BATCH_JOURNAL)
    }
    assert {
        "id",
        "idempotency_key",
        "actor_user_id",
        "request_sha256",
        "expected_snapshot",
        "state",
        "result_payload",
        "created_at",
        "updated_at",
    } <= journal_columns
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(PERSONAL_BATCH_JOURNAL)
    } == {"uq_personal_model_grant_batch_idempotency"}
    engine.dispose()

    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == PREVIOUS_HEAD
    assert CAPABILITY_RELEASE_COLUMNS.isdisjoint(_columns(engine))
    assert PERSONAL_BATCH_JOURNAL not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, CURRENT_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == CURRENT_HEAD
    assert CAPABILITY_RELEASE_COLUMNS <= _columns(engine)
    assert PERSONAL_BATCH_JOURNAL in inspect(engine).get_table_names()
    engine.dispose()


def test_protected_0041_migration_attests_before_ddl_and_acl_after_ddl() -> None:
    source = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0041_model_capability_releases.py"
    ).read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    source_gate = upgrade.index("validate_platform_migration_source_state(")
    attestation = upgrade.index("attest_platform_database_connection(")
    first_ddl = upgrade.index("op.add_column(")
    last_column = upgrade.index('"relay_capability_approved_catalog_revision"')
    post_ddl_acl = upgrade.index("validate_platform_database_acl_evidence(")
    assert source_gate < attestation < first_ddl < last_column < post_ddl_acl
    assert "policy=policy_v6" in upgrade


def test_existing_approved_revision_upgrades_fail_closed_until_live_sync_and_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "legacy-capability-release.db"
    config = _config(project_root, database_path)
    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    legacy_revision = "sha256:" + "a" * 64
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_definitions ("
                "id, slug, display_name, provider_key, capability_version, "
                "active, created_at, updated_at, published_at, billing_mode, "
                "relay_capability_revision, relay_capability_synced_at"
                ") VALUES ("
                ":id, :slug, :display_name, :provider_key, 1, 1, "
                ":created_at, :updated_at, :published_at, 'per_item', "
                ":revision, :synced_at"
                ")"
            ),
            {
                "id": "legacy-model-0041",
                "slug": "legacy.model.0041",
                "display_name": "Legacy model",
                "provider_key": "legacy-provider",
                "created_at": "2026-08-01 00:00:00",
                "updated_at": "2026-08-01 00:00:00",
                "published_at": "2026-08-01 00:00:00",
                "revision": legacy_revision,
                "synced_at": "2026-08-01 00:00:00",
            },
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT relay_capability_revision, "
                "relay_capability_candidate_revision, "
                "relay_capability_candidate_catalog_revision, "
                "relay_capability_candidate, "
                "relay_capability_candidate_synced_at, "
                "relay_capability_approved_ceiling, "
                "relay_capability_approved_catalog_revision "
                "FROM model_definitions WHERE id = :id"
            ),
            {"id": "legacy-model-0041"},
        ).mappings().one()
    assert row["relay_capability_revision"] == legacy_revision
    assert all(
        row[column] is None
        for column in CAPABILITY_RELEASE_COLUMNS
    )

    with Session(engine) as session:
        model = session.get(ModelDefinition, "legacy-model-0041")
        assert model is not None
        state = RelayCapabilityService.candidate_state(model)
        response = ModelCatalogService.response(session, model=model)
        assert state["approval_status"] == "unavailable"
        assert state["requires_approval"] is True
        assert response["relay_capability_requires_approval"] is True
        assert model.relay_capability_approved_ceiling is None
    engine.dispose()
