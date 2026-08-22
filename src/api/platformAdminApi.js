export function createPlatformAdminApi(core) {
  const { request, companyPath, makeRequestId, withQuery, PlatformApiError } = core;

  return {
    getPlatformAdminMe: ({ signal } = {}) =>
      request("/api/v1/platform-admin/me", { signal }),
    listPlatformUsers: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/platform-admin/users", filters), {
        signal,
        companyContext: false,
      }),
    setPlatformUserStatus: (
      userId,
      { expectedStatus, expectedAuthVersion, targetStatus },
      { signal } = {},
    ) => request(
      `/api/v1/platform-admin/users/${encodeURIComponent(userId)}/status`,
      {
        method: "PATCH",
        body: {
          expected_status: expectedStatus,
          expected_auth_version: expectedAuthVersion,
          target_status: targetStatus,
        },
        signal,
        companyContext: false,
      },
    ),
    getPlatformDashboard: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/platform-admin/dashboard", filters), { signal }),
    listAdminCompanies: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/platform-admin/companies", filters), { signal }),
    createAdminCompany: (
      { name, ownerEmail, ownerDisplayName },
      { signal } = {},
    ) =>
      request("/api/v1/platform-admin/companies", {
        method: "POST",
        body: {
          name,
          owner_email: ownerEmail,
          owner_display_name: ownerDisplayName,
        },
        signal,
      }),
    reissueAdminCompanyOwnerInvitation: (
      companyId,
      {
        expectedOwnerMembershipId,
        expectedOwnerUserId,
        replacementEmail = "",
        replacementDisplayName = "",
      },
      { signal } = {},
    ) => request(
      `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/owner-invitation/reissue`,
      {
        method: "POST",
        body: {
          expected_owner_membership_id: expectedOwnerMembershipId,
          expected_owner_user_id: expectedOwnerUserId,
          ...(String(replacementEmail).trim()
            ? {
                replacement_email: String(replacementEmail).trim(),
                ...(String(replacementDisplayName).trim()
                  ? { replacement_display_name: String(replacementDisplayName).trim() }
                  : {}),
              }
            : {}),
        },
        signal,
        companyContext: false,
      },
    ),
    setAdminCompanyStatus: (companyId, status, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/status`,
        { method: "PATCH", body: { status }, signal },
      ),
    rechargeAdminCompany: (
      companyId,
      { amountCents, note = "", idempotencyKey },
      { signal } = {},
    ) => {
      if (!idempotencyKey) {
        throw new PlatformApiError("充值缺少稳定的幂等键，请重新打开充值窗口", {
          code: "IDEMPOTENCY_KEY_REQUIRED",
        });
      }
      return request(
        `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/recharge`,
        {
          method: "POST",
          body: {
            amount_cents: amountCents,
            note,
            idempotency_key: idempotencyKey,
          },
          idempotencyKey,
          signal,
        },
      );
    },
    listAdminCompanyRecharges: (companyId, filters = {}, { signal } = {}) =>
      request(
        withQuery(
          `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/recharges`,
          filters,
        ),
        { signal },
      ),
    getAdminCompanyEntitlements: (companyId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/entitlements`,
        { signal },
      ),
    listAdminModels: ({ signal } = {}) =>
      request("/api/v1/platform-admin/models", { signal }),
    listAdminRelayModels: ({ signal } = {}) =>
      request("/api/v1/platform-admin/relay-models", { signal }),
    approveAdminRelayCapability: (
      modelId,
      { expectedCapabilityVersion },
      { signal } = {},
    ) =>
      request(
        `/api/v1/platform-admin/models/${encodeURIComponent(modelId)}/relay-capability`,
        {
          method: "POST",
          body: { expected_capability_version: expectedCapabilityVersion },
          signal,
        },
      ),
    createAdminModel: (
      { slug, displayName, providerKey, billingMode, capabilities = [] },
      { signal } = {},
    ) =>
      request("/api/v1/platform-admin/models", {
        method: "POST",
        body: {
          slug,
          display_name: displayName,
          provider_key: providerKey,
          billing_mode: billingMode,
          capabilities,
        },
        signal,
      }),
    updateAdminModel: (
      modelId,
      {
        displayName,
        providerKey,
        billingMode,
        expectedCapabilityVersion,
        capabilities = [],
      },
      { signal } = {},
    ) =>
      request(`/api/v1/platform-admin/models/${encodeURIComponent(modelId)}`, {
        method: "PUT",
        body: {
          display_name: displayName,
          provider_key: providerKey,
          billing_mode: billingMode,
          expected_capability_version: expectedCapabilityVersion,
          capabilities,
        },
        signal,
      }),
    publishAdminModel: (modelId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/models/${encodeURIComponent(modelId)}/publish`,
        { method: "POST", signal },
      ),
    disableAdminModel: (modelId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/models/${encodeURIComponent(modelId)}/disable`,
        { method: "POST", signal },
      ),
    deleteAdminModel: (modelId, { signal } = {}) =>
      request(`/api/v1/platform-admin/models/${encodeURIComponent(modelId)}`, {
        method: "DELETE",
        signal,
      }),
    listAdminResources: ({ signal } = {}) =>
      request("/api/v1/platform-admin/resources", { signal }),
    createAdminResource: (
      { key, kind, displayName, description = "", active = true },
      { signal } = {},
    ) =>
      request("/api/v1/platform-admin/resources", {
        method: "POST",
        body: {
          key,
          kind,
          display_name: displayName,
          description,
          active,
        },
        signal,
      }),
    updateAdminResource: (
      resourceId,
      { displayName, description = "", active = true },
      { signal } = {},
    ) =>
      request(`/api/v1/platform-admin/resources/${encodeURIComponent(resourceId)}`, {
        method: "PUT",
        body: {
          display_name: displayName,
          description,
          active,
        },
        signal,
      }),
    upsertAdminModelGrant: (companyId, payload, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/model-grants`,
        { method: "PUT", body: payload, signal },
      ),
    upsertAdminResourceGrant: (companyId, resourceId, payload, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/companies/${encodeURIComponent(companyId)}/resources/${encodeURIComponent(resourceId)}`,
        { method: "PUT", body: payload, signal },
      ),
    listAdminAuditLogs: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/platform-admin/audit-logs", filters), { signal }),
    getAdminConsumptionReport: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/reports/consumption", filters),
        { signal },
      ),
    exportAdminConsumptionReport: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/reports/consumption/export.csv", filters),
        { signal, responseType: "text" },
      ),
    listAdminChannelCosts: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/platform-admin/channel-costs", filters), {
        signal,
      }),
    createAdminChannelCost: (
      {
        amountCents,
        idempotencyKey,
        channelKey,
        channelType,
        occurredAt,
        externalReference,
        companyId: costCompanyId,
        taskId,
        relayJobId,
        note = "",
      },
      { signal } = {},
    ) => {
      if (!idempotencyKey) {
        throw new PlatformApiError("渠道成本缺少稳定的幂等键，请重新打开录入窗口", {
          code: "IDEMPOTENCY_KEY_REQUIRED",
        });
      }
      return request("/api/v1/platform-admin/channel-costs", {
        method: "POST",
        body: {
          amount_cents: amountCents,
          idempotency_key: idempotencyKey,
          channel_key: channelKey,
          channel_type: channelType,
          occurred_at: occurredAt,
          external_reference: externalReference,
          company_id: costCompanyId || null,
          task_id: taskId || null,
          relay_job_id: relayJobId || null,
          note,
        },
        idempotencyKey,
        signal,
      });
    },
    getAdminOperatingSeries: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/analytics/operating-series", filters),
        { signal },
      ),
    getAdminTaskOperations: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/analytics/task-operations", filters),
        { signal },
      ),
    getAdminModelProfitability: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/analytics/model-profitability", filters),
        { signal },
      ),
    getAdminCompanyHealth: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/analytics/company-health", filters),
        { signal },
      ),
    getAdminChannelHealth: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/analytics/channel-health", filters),
        { signal },
      ),
    listAdminRelayChannels: (filters = {}, { signal } = {}) =>
      request(withQuery("/api/v1/platform-admin/relay/channels", filters), {
        signal,
      }),
    openAdminRelayNativeConsole: ({ signal } = {}) =>
      request("/api/v1/platform-admin/relay/native-console/open", {
        method: "POST",
        body: {},
        signal,
      }),
    getAdminRelayChannel: (channelId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/relay/channels/${encodeURIComponent(channelId)}`,
        { signal },
      ),
    getAdminRelayChannelOperation: (
      channelId,
      operationId,
      { signal } = {},
    ) =>
      request(
        `/api/v1/platform-admin/relay/channels/${encodeURIComponent(channelId)}/operations/${encodeURIComponent(operationId)}`,
        { signal },
      ),
    testAdminRelayChannel: (
      channelId,
      { operationId, reason, approved },
      { signal } = {},
    ) =>
      request(
        `/api/v1/platform-admin/relay/channels/${encodeURIComponent(channelId)}/test`,
        {
          method: "POST",
          body: {
            operation_id: operationId,
            reason,
            approved,
          },
          idempotencyKey: operationId,
          signal,
        },
      ),
    setAdminRelayChannelStatus: (
      channelId,
      {
        operationId,
        reason,
        approved,
        expectedRevision,
        targetStatus,
      },
      { signal } = {},
    ) =>
      request(
        `/api/v1/platform-admin/relay/channels/${encodeURIComponent(channelId)}/status`,
        {
          method: "POST",
          body: {
            operation_id: operationId,
            reason,
            approved,
            expected_revision: expectedRevision,
            target_status: targetStatus,
          },
          idempotencyKey: operationId,
          signal,
        },
      ),
    listAdminRelayUnknownSubmissions: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/relay/submission-unknown", filters),
        { signal },
      ),
    getAdminRelayUnknownSubmission: (jobId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/relay/submission-unknown/${encodeURIComponent(jobId)}`,
        { signal },
      ),
    getAdminRelayUnknownSubmissionResult: (
      jobId,
      { operationId } = {},
      { signal } = {},
    ) => request(
      withQuery(
        `/api/v1/platform-admin/relay/submission-unknown/${encodeURIComponent(jobId)}/result`,
        { operation_id: operationId },
      ),
      { signal },
    ),
    resolveAdminRelayUnknownSubmission: (
      jobId,
      {
        outcome,
        upstream_task_id,
        expected_route_id,
        expected_submission_attempt,
        expected_reconciliation_token,
        verification_reference,
        reason,
      },
      { signal } = {},
    ) => request(
      `/api/v1/platform-admin/relay/submission-unknown/${encodeURIComponent(jobId)}/resolve`,
      {
        method: "POST",
        // Intentionally no transport retry: a lost response is reconciled by
        // reading the signed result receipt, never by repeating this POST.
        body: {
          outcome,
          upstream_task_id,
          expected_route_id,
          expected_submission_attempt,
          expected_reconciliation_token,
          verification_reference,
          reason,
        },
        signal,
      },
    ),
    listAdminRelayCallbackDeadLetters: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/relay/callback-dead-letters", filters),
        { signal },
      ),
    getAdminRelayCallbackDeadLetter: (eventId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/relay/callback-dead-letters/${encodeURIComponent(eventId)}`,
        { signal },
      ),
    getAdminRelayCallbackRedriveResult: (
      eventId,
      { operationId } = {},
      { signal } = {},
    ) => request(
      withQuery(
        `/api/v1/platform-admin/relay/callback-dead-letters/${encodeURIComponent(eventId)}/result`,
        { operation_id: operationId },
      ),
      { signal },
    ),
    redriveAdminRelayCallbackDeadLetter: (
      eventId,
      { operation_id, actor, reason, approved },
      { signal } = {},
    ) => request(
      `/api/v1/platform-admin/relay/callback-dead-letters/${encodeURIComponent(eventId)}/redrive`,
      {
        method: "POST",
        // Exactly one POST. Any ambiguous transport outcome is reconciled by
        // GET /result with the stable Platform operation_id.
        body: { operation_id, actor, reason, approved },
        signal,
      },
    ),
    getAdminDataReadiness: ({ signal } = {}) =>
      request("/api/v1/platform-admin/analytics/data-readiness", { signal }),
    getAdminExceptionCenter: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/analytics/exceptions", filters),
        { signal },
      ),
    reconcileAdminDownloadGatewayAttempt: (attemptId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/download-gateway-registration-attempts/${encodeURIComponent(attemptId)}/reconcile`,
        { method: "POST", signal },
      ),
    getAdminEntitlementMatrix: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/entitlements/matrix", filters),
        { signal },
      ),
    getAdminEntitlementCoverage: (filters = {}, { signal } = {}) =>
      request(
        withQuery("/api/v1/platform-admin/entitlements/coverage", filters),
        { signal },
      ),
    listPlatformAdminPermissionCatalog: ({ signal } = {}) =>
      request("/api/v1/platform-admin/access/permissions", { signal }),
    listPlatformAdminRoles: ({ signal } = {}) =>
      request("/api/v1/platform-admin/access/roles", { signal }),
    createPlatformAdminRole: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/access/roles", {
        method: "POST",
        body,
        signal,
      }),
    replacePlatformAdminRole: (roleId, body, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/access/roles/${encodeURIComponent(roleId)}`,
        { method: "PUT", body, signal },
      ),
    listPlatformAdministrators: ({ signal } = {}) =>
      request("/api/v1/platform-admin/access/users", { signal }),
    getPlatformAdministratorAccess: (userId, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/access/users/${encodeURIComponent(userId)}`,
        { signal },
      ),
    replacePlatformAdministratorAccess: (userId, body, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/access/users/${encodeURIComponent(userId)}`,
        { method: "PUT", body, signal },
      ),
    setPlatformAdministratorStatus: (userId, body, { signal } = {}) =>
      request(
        `/api/v1/platform-admin/access/users/${encodeURIComponent(userId)}/status`,
        { method: "PUT", body, signal },
      ),
    previewAdminEntitlementBatch: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/entitlements/batch/preview", {
        method: "POST",
        body,
        signal,
      }),
    executeAdminEntitlementBatch: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/entitlements/batch/execute", {
        method: "POST",
        body,
        idempotencyKey: body?.idempotency_key,
        signal,
      }),
    previewAdminEntitlementCopy: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/entitlements/copy/preview", {
        method: "POST",
        body,
        signal,
      }),
    executeAdminEntitlementCopy: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/entitlements/copy/execute", {
        method: "POST",
        body,
        idempotencyKey: body?.idempotency_key,
        signal,
      }),
    previewAdminEntitlementTemplate: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/entitlements/templates/preview", {
        method: "POST",
        body,
        signal,
      }),
    executeAdminEntitlementTemplate: (body, { signal } = {}) =>
      request("/api/v1/platform-admin/entitlements/templates/execute", {
        method: "POST",
        body,
        idempotencyKey: body?.idempotency_key,
        signal,
      }),
  };
}
