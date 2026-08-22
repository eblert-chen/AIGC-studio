"""Stable identities for the completed new-api Relay cutover.

These values are deliberately code-owned.  A protected Platform release may
rotate credentials or move the canonical origin, but it must not silently
reinterpret persisted task affinity as another backend or contract.
"""

NEW_API_RELAY_BACKEND_ID = "new-api-v1"
NEW_API_RELAY_CONTRACT_REVISION = "generations.v1"

# Historical rows created before the cutover retain this immutable affinity.
# Protected runtimes never configure a callable client for it.
LEGACY_RELAY_BACKEND_ID = "legacy-default-v1"

RELAY_BACKEND_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
RELAY_CONTRACT_REVISION_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
