-- The new-api Relay roles are deliberately bound to the new_api database.
-- PostgreSQL grants PUBLIC CONNECT/TEMP on the maintenance/template databases
-- by default; revoke those cluster-wide defaults before role provisioning so
-- the native postcondition can prove that no protected role crosses databases.
REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE template0 FROM PUBLIC;
REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE template1 FROM PUBLIC;
