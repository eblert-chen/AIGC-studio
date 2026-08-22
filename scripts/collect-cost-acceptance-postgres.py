"""Emit secret-free fingerprints for the two cost-acceptance PostgreSQLs."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import psycopg


def _connect_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def _sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _fingerprint(env_name: str) -> dict[str, object]:
    raw_url = os.environ.get(env_name, "")
    if not raw_url:
        raise RuntimeError("required PostgreSQL connection is unavailable")
    with psycopg.connect(_connect_url(raw_url), connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_setting('server_version_num')::integer,
                       current_database(),
                       system_identifier::text,
                       pg_postmaster_start_time(),
                       COALESCE(inet_server_addr()::text, 'local'),
                       COALESCE(inet_server_port(), 0)
                FROM pg_control_system()
                """
            )
            (
                server_version_num,
                database_name,
                system_identifier,
                started_at,
                server_address,
                server_port,
            ) = cursor.fetchone()
            cursor.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            has_alembic = bool(cursor.fetchone()[0])
            migration_versions: list[str] = []
            if has_alembic:
                cursor.execute(
                    """
                    SELECT version_num
                    FROM alembic_version
                    ORDER BY version_num
                    """
                )
                migration_versions = [row[0] for row in cursor.fetchall()]
    return {
        "server_version_num": server_version_num,
        "database_name": database_name,
        "system_identifier_sha256": _sha256(system_identifier),
        "postmaster_started_at_utc": started_at.isoformat(),
        "server_endpoint_sha256": _sha256(f"{server_address}:{server_port}"),
        "migration_versions": migration_versions,
    }


def main() -> int:
    try:
        platform = _fingerprint("COST_ACCEPTANCE_PLATFORM_DATABASE_URL")
        relay = _fingerprint("COST_ACCEPTANCE_RELAY_DATABASE_URL")
        if platform["system_identifier_sha256"] == relay["system_identifier_sha256"]:
            raise RuntimeError("cost acceptance requires two distinct PostgreSQL instances")
        print(
            json.dumps(
                {"platform": platform, "relay": relay},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception:
        print("cost-acceptance PostgreSQL fingerprint collection failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
