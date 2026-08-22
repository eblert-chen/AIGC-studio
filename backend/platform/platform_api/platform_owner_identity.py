from __future__ import annotations

from collections.abc import Collection
import hmac


def is_platform_owner_identity(
    *,
    issuer: str | None,
    subject: str | None,
    configured_issuer: str | None,
    configured_subjects: Collection[str],
) -> bool:
    """Match the non-delegable owner identity as one OIDC ``issuer + sub``.

    OIDC subjects are scoped to an issuer.  Comparing ``sub`` alone lets a
    different identity provider mint the same text and cross the owner
    boundary, so every production owner decision uses this exact pair.
    """

    if not issuer or not subject or not configured_issuer:
        return False
    return hmac.compare_digest(issuer, configured_issuer) and subject in set(
        configured_subjects
    )
