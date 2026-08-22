export function createSessionPersonalApi(core) {
  const { request, companyPath, makeRequestId, withQuery, PlatformApiError } = core;

  return {
    getSessionSurfaces: ({ signal } = {}) =>
      request("/api/v1/session/surfaces", { signal, companyContext: false }),
    getPersonalMe: ({ signal } = {}) =>
      request("/api/v1/personal/me", { signal, companyContext: false }),
    getPersonalWallet: ({ signal } = {}) =>
      request("/api/v1/personal/wallet", { signal, companyContext: false }),
    listPersonalModels: ({ signal } = {}) =>
      request("/api/v1/personal/models", { signal, companyContext: false }),
    listPersonalTasks: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/personal/tasks", filters), {
        signal,
        companyContext: false,
      }),
    getPersonalTask: (taskId, { signal } = {}) =>
      request(`/api/v1/personal/tasks/${encodeURIComponent(taskId)}`, {
        signal,
        companyContext: false,
      }),
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
      }),
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
        });
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
