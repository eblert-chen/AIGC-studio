export const PREVIEW_EXPIRY_SAFETY_MS = 1_000;

function finiteExpiry(value) {
  const expiry = Number(value);
  return Number.isFinite(expiry) ? expiry : 0;
}

export function createPreviewLease(url, expiresSeconds, now = Date.now()) {
  const normalizedUrl = String(url || "").trim();
  const ttlSeconds = Number(expiresSeconds);
  if (!normalizedUrl || !Number.isFinite(ttlSeconds) || ttlSeconds <= 0) return null;
  return {
    url: normalizedUrl,
    expiresAt: now + ttlSeconds * 1_000,
  };
}

export function activePreviewUrl(
  lease,
  now = Date.now(),
  safetyMs = PREVIEW_EXPIRY_SAFETY_MS,
) {
  const url = String(lease?.url || "").trim();
  const expiresAt = finiteExpiry(lease?.expiresAt);
  return url && expiresAt > now + safetyMs ? url : "";
}

export function removePreviewLease(leases, key) {
  if (!leases || !Object.hasOwn(leases, key)) return leases;
  const next = { ...leases };
  delete next[key];
  return next;
}

export function removeExpiredPreviewLeases(
  leases,
  now = Date.now(),
  safetyMs = PREVIEW_EXPIRY_SAFETY_MS,
) {
  if (!leases || typeof leases !== "object") return {};
  let next = leases;
  for (const [key, lease] of Object.entries(leases)) {
    if (activePreviewUrl(lease, now, safetyMs)) continue;
    if (next === leases) next = { ...leases };
    delete next[key];
  }
  return next;
}

export function nextPreviewCleanupDelay(
  leases,
  now = Date.now(),
  safetyMs = PREVIEW_EXPIRY_SAFETY_MS,
) {
  let earliest = Number.POSITIVE_INFINITY;
  for (const lease of Object.values(leases || {})) {
    const expiresAt = finiteExpiry(lease?.expiresAt);
    if (!expiresAt) return 0;
    earliest = Math.min(earliest, expiresAt - safetyMs);
  }
  if (!Number.isFinite(earliest)) return null;
  return Math.max(0, earliest - now);
}
