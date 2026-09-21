# Commercial approval revisions

When a supplier account, credential version, or routing release changes, the
old approval remains an immutable historical decision. The owner submits a
new approval through the existing `PUT /api/v1/platform-admin/models/{model_id}/commercial-release-plan`
endpoint, using a fresh idempotency key and `supersedes_plan_id` equal to the
latest unreleased plan ID. All pricing, capability and supplier evidence
requirements still apply. Missing, stale, unrelated, or released predecessors
return HTTP 409. Reusing the same predecessor concurrently creates at most one
successor.

The response adds `revision` and `supersedes_plan_id`; a superseded plan's
execution state is `superseded`. Old plan IDs, approval users, content hashes,
prices, and supplier snapshots are retained. New approvals bind the current
supplier snapshot. Only the latest approval can publish. Exact retries of
post-0055 approval requests return their original receipt, including after
route changes, replacement, or publication; changed requests using that key
are rejected. New approvals do not replace an already published plan.

Commercial approval and reconciliation acquire company locks, then model
locks, then execution locks. The HTTP reconciliation entry acquires company
locks before catalog materialization in its transaction. Background catalog
materialization commits in its separate transaction before commercial
reconciliation begins. The execution JOIN uses `FOR UPDATE OF` explicitly.

Migration 0055 retains historical rows, replaces candidate-wide uniqueness
with candidate/revision uniqueness, binds each successor to one predecessor,
and prevents superseded executions from reopening. Its SQLite path repairs
the three historical commercial evidence triggers' missing `NEW.` prefixes;
its PostgreSQL path compares publication JSON as JSONB without rewriting
stored receipts. ORM optional evidence writes SQL NULL, and plans are inserted
before their execution foreign keys. Historical migrations remain unchanged.
Downgrade refuses after a new approval has been persisted.

The v20 database policy has the same table/role ACL surface and an explicitly
UNQUALIFIED catalog digest. It rejects protected runtime attestation until
separately qualified. The local PostgreSQL 18.3 tests prove migration,
revision concurrency, and the deadlock regression; they are not PostgreSQL 16,
TLS, release-proof, paid-model, or production cutover evidence.

Regression files:

- `tests/test_commercial_plan_revisions.py`: API replacement, history, exact
  retry, stale predecessor, released-plan protection, and emitted lock SQL.
- `tests/test_commercial_plan_revisions_migration.py`: historical rows,
  database guards, downgrade, metadata alignment, and unqualified policy.
- `tests/test_commercial_plan_revisions_postgres.py`: opt-in isolated
  `PLATFORM_REVIEW_POSTGRES_URL` database. Confirms the former lock order raises
  `40P01`, the corrected order completes, and concurrent successors yield
  one HTTP 200 and one HTTP 409. Uses only mocked Relay evidence.
