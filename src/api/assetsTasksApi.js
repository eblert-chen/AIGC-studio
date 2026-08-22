export function createAssetsTasksApi(core) {
  const { request, companyPath, makeRequestId, withQuery, PlatformApiError } = core;

  return {
    listModels: ({ signal } = {}) => request(companyPath("/models"), { signal }),
    listAssets: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/assets"), filters), { signal }),
    uploadAsset: async (
      file,
      mediaType,
      { signal, idempotencyKey } = {},
    ) => {
      if (typeof FormData === "undefined") {
        throw new PlatformApiError("当前浏览器不支持文件上传", {
          code: "FORM_DATA_UNAVAILABLE",
        });
      }
      const form = new FormData();
      form.append("file", file, file?.name || "upload.bin");
      if (mediaType) form.append("media_type", mediaType);
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      const submit = () =>
        request(companyPath("/assets"), {
          method: "POST",
          body: form,
          idempotencyKey: stableIdempotencyKey,
          signal,
          timeoutMs: 5 * 60_000,
        });
      try {
        return await submit();
      } catch (error) {
        const uncertain =
          error instanceof PlatformApiError &&
          (error.status >= 500 ||
            error.status === 0 ||
            ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(
              error.code,
            ));
        if (signal?.aborted || !uncertain) throw error;
        return submit();
      }
    },
    getAssetPreview: (assetId, { signal } = {}) =>
      request(
        companyPath(`/assets/${encodeURIComponent(assetId)}/preview`),
        { signal },
      ),
    getAssetDownload: (assetId, { signal } = {}) =>
      request(
        companyPath(`/assets/${encodeURIComponent(assetId)}/download`),
        { signal },
      ),
    deleteAsset: (assetId, { signal } = {}) =>
      request(companyPath(`/assets/${encodeURIComponent(assetId)}`), {
        method: "DELETE",
        signal,
      }),
    listTaskHistory: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/task-history"), filters), { signal }),
    listArtworks: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/artworks"), filters), { signal }),
    listTasks: ({ signal } = {}) => request(companyPath("/tasks"), { signal }),
    getTask: (taskId, { signal, scope } = {}) =>
      request(
        withQuery(companyPath(`/tasks/${encodeURIComponent(taskId)}`), { scope }),
        { signal },
      ),
    getArtifactPreview: (taskId, assetId, { signal, scope } = {}) =>
      request(
        withQuery(
          companyPath(
            `/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(assetId)}/preview`,
          ),
          { scope },
        ),
        { signal },
      ),
    getArtifactDownload: (taskId, assetId, { signal, scope } = {}) =>
      request(
        withQuery(
          companyPath(
            `/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(assetId)}/download`,
          ),
          { scope },
        ),
        { signal },
      ),
    promoteArtifactToInputAsset: async (
      taskId,
      assetId,
      { idempotencyKey, signal, scope } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      const submit = () =>
        request(
          withQuery(
            companyPath(
              `/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(assetId)}/input-asset`,
            ),
            { scope },
          ),
          {
            method: "POST",
            body: { idempotency_key: stableIdempotencyKey },
            idempotencyKey: stableIdempotencyKey,
            signal,
            timeoutMs: 5 * 60_000,
          },
        );
      try {
        return await submit();
      } catch (error) {
        const uncertain =
          error instanceof PlatformApiError &&
          (error.status >= 500 ||
            error.status === 0 ||
            ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(
              error.code,
            ));
        if (signal?.aborted || !uncertain) throw error;
        return submit();
      }
    },
    createTask: async (
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
        request(companyPath("/tasks"), {
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
        if (signal?.aborted || !isUncertain(error)) {
          throw error;
        }
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
    cancelTask: (taskId, { signal } = {}) =>
      request(companyPath(`/tasks/${encodeURIComponent(taskId)}/cancel`), {
        method: "POST",
        signal,
      }),
  };
}
