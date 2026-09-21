const PERSONAL_BILLING_UNIT = "POINT";
const PERSONAL_BILLING_VERSION = 2;

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function conflictsWithPersonalPointContract(source) {
  if (!isRecord(source)) return true;
  const declaredUnit = String(source.billing_unit ?? "").trim().toUpperCase();
  const declaredVersion = source.billing_version;
  return (declaredUnit && declaredUnit !== PERSONAL_BILLING_UNIT)
    || (declaredVersion !== null
      && declaredVersion !== undefined
      && Number(declaredVersion) !== PERSONAL_BILLING_VERSION);
}

/**
 * Personal endpoints are a dedicated, server-authorized points boundary. Older
 * deployments predate explicit billing metadata, so the client materializes the
 * route's fixed POINT/v2 contract. Conflicting server evidence is never replaced.
 */
export function adaptPersonalPointRecord(source) {
  if (!isRecord(source) || conflictsWithPersonalPointContract(source)) return source;
  return {
    ...source,
    billing_unit: PERSONAL_BILLING_UNIT,
    billing_version: PERSONAL_BILLING_VERSION,
    billing_scope: source.billing_scope || "personal",
  };
}

export function adaptPersonalPointCollection(payload) {
  if (Array.isArray(payload)) return payload.map(adaptPersonalPointRecord);
  if (!isRecord(payload) || conflictsWithPersonalPointContract(payload)) return payload;
  const adapted = adaptPersonalPointRecord(payload);
  return Array.isArray(payload.items)
    ? { ...adapted, items: payload.items.map(adaptPersonalPointRecord) }
    : adapted;
}

export function createSessionPersonalApi(core) {
  const { request, companyPath, makeRequestId, withQuery, PlatformApiError } = core;

  return {
    getSessionSurfaces: ({ signal } = {}) =>
      request("/api/v1/session/surfaces", { signal, companyContext: false }),
    getPersonalMe: ({ signal } = {}) =>
      request("/api/v1/personal/me", { signal, companyContext: false }),
    getPersonalWallet: ({ signal } = {}) =>
      request("/api/v1/personal/wallet", { signal, companyContext: false })
        .then(adaptPersonalPointRecord),
    listPersonalModels: ({ signal } = {}) =>
      request("/api/v1/personal/models", { signal, companyContext: false })
        .then(adaptPersonalPointCollection),
    listPersonalModelCatalog: ({ signal } = {}) =>
      request("/api/v1/personal/model-catalog", { signal, companyContext: false })
        .then(adaptPersonalPointCollection),
    listPersonalTasks: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/personal/tasks", filters), {
        signal,
        companyContext: false,
      }).then(adaptPersonalPointCollection),
    getPersonalTask: (taskId, { signal } = {}) =>
      request(`/api/v1/personal/tasks/${encodeURIComponent(taskId)}`, {
        signal,
        companyContext: false,
      }).then(adaptPersonalPointRecord),
    getPersonalArtifactPreview: (taskId, assetId, { signal } = {}) =>
      request(
        `/api/v1/personal/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(assetId)}/preview`,
        { signal, companyContext: false },
      ),
    getPersonalArtifactDownload: (taskId, assetId, { signal } = {}) =>
      request(
        `/api/v1/personal/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(assetId)}/download`,
        { signal, companyContext: false },
      ),
    listPersonalArtworks: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/personal/artworks", filters), {
        signal,
        companyContext: false,
      }).then(adaptPersonalPointCollection),
    createPersonalTask: async (
      {
        modelId,
        requestPayload,
        expectedCapabilityVersion,
        expectedQuoteRevision,
      },
      { idempotencyKey, signal } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      const submit = () =>
        request("/api/v1/personal/tasks", {
          method: "POST",
          body: {
            model_id: modelId,
            idempotency_key: stableIdempotencyKey,
            request_payload: requestPayload,
            ...(Number.isInteger(expectedCapabilityVersion)
              ? { expected_capability_version: expectedCapabilityVersion }
              : {}),
            ...(typeof expectedQuoteRevision === "string" && expectedQuoteRevision
              ? { expected_quote_revision: expectedQuoteRevision }
              : {}),
          },
          idempotencyKey: stableIdempotencyKey,
          signal,
          companyContext: false,
        }).then(adaptPersonalPointRecord);
      const isUncertain = (error) =>
        error instanceof PlatformApiError &&
        (error.status >= 500 ||
          error.status === 0 ||
          ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(
            error.code,
          ));
      try {
        return await submit();
      } catch (error) {
        if (signal?.aborted || !isUncertain(error)) throw error;
        try {
          return await submit();
        } catch (retryError) {
          if (retryError && typeof retryError === "object") {
            retryError.submissionUncertain = true;
          }
          throw retryError;
        }
      }
    },
  };
}
