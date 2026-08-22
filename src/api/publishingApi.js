export function createPublishingApi(core) {
  const { request, companyPath, makeRequestId, withQuery, PlatformApiError } = core;

  return {
    listPublisherConnections: (filters = {}, { signal } = {}) =>
      request(
        withQuery(companyPath("/publishing/connections"), filters),
        { signal },
      ),
    listPublisherOAuthProviders: ({ signal } = {}) =>
      request(companyPath("/publishing/connections/oauth/providers"), { signal }),
    startPublisherOAuth: ({ provider }, { signal } = {}) =>
      request(companyPath("/publishing/connections/oauth/start"), {
        method: "POST",
        body: { provider },
        signal,
      }),
    createPublisherConnection: (
      typeof import.meta.env !== "undefined" && import.meta.env.PROD
    )
      ? () => {
        throw new PlatformApiError("生产环境禁止创建 Mock 发布连接", {
          code: "PRODUCTION_MOCK_PUBLISHER_FORBIDDEN",
        });
      }
      : (
        { provider = "mock", displayName },
        { signal } = {},
      ) => {
        if (provider !== "mock") {
          throw new PlatformApiError("浏览器目前只能创建明确标记的测试发布连接", {
            code: "UNSUPPORTED_PUBLISHER_CONNECTION_PROVIDER",
          });
        }
        const developmentBuild = typeof import.meta.env !== "undefined"
          && import.meta.env.DEV
          && !import.meta.env.PROD;
        if (!developmentBuild) {
          throw new PlatformApiError("Mock 发布连接只允许在开发构建中创建", {
            code: "MOCK_PUBLISHER_CONNECTIONS_DEVELOPMENT_ONLY",
          });
        }
        return request(companyPath("/publishing/connections"), {
          method: "POST",
          body: { provider, display_name: displayName },
          signal,
        });
      },
    deletePublisherConnection: (connectionId, { signal } = {}) =>
      request(
        companyPath(
          `/publishing/connections/${encodeURIComponent(connectionId)}`,
        ),
        { method: "DELETE", signal },
      ),
    listPublicationJobs: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/publishing/jobs"), filters), { signal }),
    createPublicationJob: (
      {
        artifactId,
        connectionId,
        title = "",
        caption = "",
        scheduledAt = null,
        timezone = "Asia/Shanghai",
        idempotencyKey,
      },
      { signal } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      return request(companyPath("/publishing/jobs"), {
        method: "POST",
        body: {
          artifact_id: artifactId,
          connection_id: connectionId,
          idempotency_key: stableIdempotencyKey,
          title,
          caption,
          scheduled_at: scheduledAt,
          timezone,
        },
        idempotencyKey: stableIdempotencyKey,
        signal,
      });
    },
    getPublicationJob: (jobId, { signal } = {}) =>
      request(
        companyPath(`/publishing/jobs/${encodeURIComponent(jobId)}`),
        { signal },
      ),
    approvePublicationJob: (jobId, { signal } = {}) =>
      request(
        companyPath(`/publishing/jobs/${encodeURIComponent(jobId)}/approve`),
        { method: "POST", signal },
      ),
    cancelPublicationJob: (jobId, { signal } = {}) =>
      request(
        companyPath(`/publishing/jobs/${encodeURIComponent(jobId)}/cancel`),
        { method: "POST", signal },
      ),
    retryPublicationJob: (jobId, { signal } = {}) =>
      request(
        companyPath(`/publishing/jobs/${encodeURIComponent(jobId)}/retry`),
        { method: "POST", signal },
      ),
    reconcilePublicationJob: (
      jobId,
      {
        outcome,
        externalPostId,
        externalPostUrl,
        errorCode,
        errorMessage,
      },
      { signal } = {},
    ) => {
      if (!["published", "failed"].includes(outcome)) {
        throw new PlatformApiError("人工核销结果必须是已发布或失败", {
          code: "INVALID_RECONCILIATION_OUTCOME",
        });
      }
      if (outcome === "published" && !String(externalPostId || "").trim()) {
        throw new PlatformApiError("核销为已发布时必须填写渠道作品号", {
          code: "EXTERNAL_POST_ID_REQUIRED",
        });
      }
      const body = outcome === "published"
        ? {
            outcome,
            external_post_id: String(externalPostId).trim(),
            ...(String(externalPostUrl || "").trim()
              ? { external_post_url: String(externalPostUrl).trim() }
              : {}),
          }
        : {
            outcome,
            ...(String(errorCode || "").trim()
              ? { error_code: String(errorCode).trim() }
              : {}),
            ...(String(errorMessage || "").trim()
              ? { error_message: String(errorMessage).trim() }
              : {}),
          };
      return request(
        companyPath(`/publishing/jobs/${encodeURIComponent(jobId)}/reconcile`),
        { method: "POST", body, signal },
      );
    },
  };
}
