\set ON_ERROR_STOP on

SELECT (
  rolname = 'relay_download_edge'
  AND NOT rolsuper
  AND NOT rolcreatedb
  AND NOT rolcreaterole
  AND NOT rolinherit
  AND NOT rolreplication
  AND NOT rolbypassrls
) AS role_flags_ok
FROM pg_roles
WHERE rolname = 'relay_download_edge'
\gset
\if :role_flags_ok
\else
  \echo relay_download_edge role flags are unsafe
  \quit 1
\endif

INSERT INTO platform_relay_external_deliveries
  (event_kind, event_id, request_id, state, attempts, max_attempts,
   available_at, claim_token, response_status, last_error, created_at, updated_at)
VALUES
  ('provider_alert', '10000000-0000-4000-8000-000000000001', 'privilege-alert', 'pending', 0, 3,
   clock_timestamp(), '', 0, '', clock_timestamp(), clock_timestamp()),
  ('channel_cost', '10000000-0000-4000-8000-000000000002', 'privilege-cost', 'pending', 0, 3,
   clock_timestamp(), '', 0, '', clock_timestamp(), clock_timestamp());

SET ROLE relay_download_edge;

SELECT (
  current_user = 'relay_download_edge'
  AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
  AND has_table_privilege(current_user, 'public.platform_download_edge_tickets', 'SELECT')
  AND has_table_privilege(current_user, 'public.platform_download_edge_tickets', 'INSERT')
  AND has_column_privilege(current_user, 'public.platform_download_edge_tickets', 'state', 'UPDATE')
  AND NOT has_column_privilege(current_user, 'public.platform_download_edge_tickets', 'registration_request_id', 'UPDATE')
  AND NOT has_column_privilege(current_user, 'public.platform_download_edge_tickets', 'source_url_ciphertext', 'UPDATE')
  AND has_table_privilege(current_user, 'public.platform_relay_external_deliveries', 'SELECT')
  AND has_table_privilege(current_user, 'public.platform_relay_external_deliveries', 'INSERT')
  AND has_column_privilege(current_user, 'public.platform_relay_external_deliveries', 'state', 'UPDATE')
  AND NOT has_column_privilege(current_user, 'public.platform_relay_external_deliveries', 'event_kind', 'UPDATE')
  AND NOT has_column_privilege(current_user, 'public.platform_relay_external_deliveries', 'event_id', 'UPDATE')
  AND NOT has_column_privilege(current_user, 'public.platform_relay_external_deliveries', 'request_id', 'UPDATE')
  AND has_table_privilege(current_user, 'public.platform_download_completion_events', 'INSERT')
  AND has_table_privilege(current_user, 'public.platform_download_completion_proofs', 'INSERT')
  AND NOT has_table_privilege(current_user, 'public.channels', 'SELECT')
  AND NOT has_table_privilege(current_user, 'public.tokens', 'SELECT')
  AND NOT has_table_privilege(current_user, 'public.users', 'SELECT')
  AND NOT has_table_privilege(current_user, 'public.platform_channel_cost_events', 'SELECT')
  AND NOT has_table_privilege(current_user, 'public.platform_provider_alert_events', 'SELECT')
) AS privilege_shape_ok
\gset
\if :privilege_shape_ok
\else
  \echo relay_download_edge privilege shape is unsafe
  \quit 1
\endif

SELECT (count(*) = 0) AS foreign_outbox_hidden
FROM platform_relay_external_deliveries
WHERE event_kind IN ('provider_alert', 'channel_cost')
\gset
\if :foreign_outbox_hidden
\else
  \echo RLS exposed another delivery domain
  \quit 1
\endif

INSERT INTO platform_download_edge_tickets (
  id, token_sha256, registration_request_id, registration_payload_sha256,
  download_record_id, company_id, task_id, asset_id, expected_size_bytes,
  artifact_sha256, obs_bucket, obs_object_key, obs_version_id,
  issuance_request_id, transfer_reference, source_url_sha256,
  source_expires_at, source_url_ciphertext, source_url_nonce, state,
  claim_token, gateway_request_id, failure_code, issued_at, expires_at,
  created_at, updated_at
) VALUES (
  '20000000-0000-4000-8000-000000000001', repeat('1', 64),
  '20000000-0000-4000-8000-000000000002', repeat('2', 64),
  '20000000-0000-4000-8000-000000000003',
  '20000000-0000-4000-8000-000000000004',
  '20000000-0000-4000-8000-000000000005', 'asset-privilege-test', 4,
  repeat('3', 64), 'artifact-bucket', 'results/test.mp4', '',
  'issuance-privilege-test', '20000000-0000-4000-8000-000000000006',
  repeat('4', 64), clock_timestamp() + interval '10 minutes',
  decode(repeat('ab', 32), 'hex'), decode(repeat('cd', 12), 'hex'), 'pending',
  '', '', '', clock_timestamp(), clock_timestamp() + interval '5 minutes',
  clock_timestamp(), clock_timestamp()
);

UPDATE platform_download_edge_tickets
SET state = 'claimed',
    claim_token = '20000000-0000-4000-8000-000000000007',
    claimed_at = clock_timestamp(),
    claim_expires_at = clock_timestamp() + interval '1 minute',
    gateway_request_id = issuance_request_id,
    updated_at = clock_timestamp()
WHERE id = '20000000-0000-4000-8000-000000000001';

INSERT INTO platform_download_completion_events (
  id, ticket_id, download_record_id, company_id, task_id, asset_id,
  issuance_request_id, transfer_reference, gateway_request_id, obs_bucket,
  obs_object_key, obs_version_id, bytes_sent, expected_size_bytes,
  artifact_sha256, http_status, transfer_scope, completed_at,
  payload_json, payload_sha256, created_at
) VALUES (
  '20000000-0000-4000-8000-000000000008',
  '20000000-0000-4000-8000-000000000001',
  '20000000-0000-4000-8000-000000000003',
  '20000000-0000-4000-8000-000000000004',
  '20000000-0000-4000-8000-000000000005', 'asset-privilege-test',
  'issuance-privilege-test', '20000000-0000-4000-8000-000000000006',
  'issuance-privilege-test', 'artifact-bucket', 'results/test.mp4', '',
  4, 4, repeat('3', 64), 200, 'full_body', clock_timestamp(),
  '{"schema_version":1}', repeat('5', 64), clock_timestamp()
);

INSERT INTO platform_relay_external_deliveries
  (event_kind, event_id, request_id, state, attempts, max_attempts,
   available_at, claim_token, response_status, last_error, created_at, updated_at)
VALUES
  ('download_completion', '20000000-0000-4000-8000-000000000008',
   'edge-download-completion-20000000-0000-4000-8000-000000000008',
   'pending', 0, 3, clock_timestamp(), '', 0, '', clock_timestamp(), clock_timestamp());

UPDATE platform_download_edge_tickets
SET state = 'completed', claim_token = '', claimed_at = NULL,
    claim_expires_at = NULL, completed_at = clock_timestamp(),
    updated_at = clock_timestamp()
WHERE id = '20000000-0000-4000-8000-000000000001';

DO $$
BEGIN
  BEGIN
    UPDATE platform_download_edge_tickets SET registration_request_id = registration_request_id;
    RAISE EXCEPTION 'ticket identity update unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    UPDATE platform_download_edge_tickets SET source_url_ciphertext = source_url_ciphertext;
    RAISE EXCEPTION 'ticket ciphertext update unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    UPDATE platform_relay_external_deliveries SET event_kind = event_kind;
    RAISE EXCEPTION 'outbox identity update unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    INSERT INTO platform_relay_external_deliveries
      (event_kind, event_id, request_id, state, attempts, max_attempts,
       available_at, claim_token, response_status, last_error, created_at, updated_at)
    VALUES ('provider_alert', '20000000-0000-4000-8000-000000000009',
      'forbidden-alert', 'pending', 0, 3, clock_timestamp(), '', 0, '',
      clock_timestamp(), clock_timestamp());
    RAISE EXCEPTION 'RLS-forbidden outbox insert unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege OR check_violation THEN NULL;
  END;
  BEGIN
    CREATE TABLE relay_download_edge_forbidden_ddl (id integer);
    RAISE EXCEPTION 'DDL unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    TRUNCATE platform_download_edge_tickets;
    RAISE EXCEPTION 'TRUNCATE unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END
$$;

RESET ROLE;
