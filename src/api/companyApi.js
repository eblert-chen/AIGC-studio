export function createCompanyApi(core) {
  const { request, companyPath, makeRequestId, withQuery, PlatformApiError } = core;

  return {
    getCompanyMe: ({ signal } = {}) => request(companyPath("/me"), { signal }),
    listMembers: ({ signal } = {}) => request(companyPath("/members"), { signal }),
    listPermissionCatalog: ({ signal } = {}) =>
      request(companyPath("/permissions"), { signal }),
    getMemberPermissions: (membershipId, { signal } = {}) =>
      request(companyPath(`/members/${encodeURIComponent(membershipId)}/permissions`), { signal }),
    createMember: ({ email, displayName, primaryRole = "operator" }, { signal } = {}) =>
      request(companyPath("/members"), {
        method: "POST",
        body: { email, display_name: displayName, primary_role: primaryRole },
        signal,
      }),
    listInvitations: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/invitations"), filters), { signal }),
    createInvitation: (
      {
        email,
        displayName,
        primaryRole = "operator",
        idempotencyKey,
        expiresInHours,
      },
      { signal } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      return request(companyPath("/invitations"), {
        method: "POST",
        idempotencyKey: stableIdempotencyKey,
        body: {
          email,
          display_name: displayName,
          primary_role: primaryRole,
          idempotency_key: stableIdempotencyKey,
          ...(Number.isInteger(expiresInHours)
            ? { expires_in_hours: expiresInHours }
            : {}),
        },
        signal,
      });
    },
    reissueInvitation: (invitationId, { signal } = {}) =>
      request(
        companyPath(`/invitations/${encodeURIComponent(invitationId)}/reissue`),
        { method: "POST", body: {}, signal },
      ),
    revokeInvitation: (invitationId, { signal } = {}) =>
      request(
        companyPath(`/invitations/${encodeURIComponent(invitationId)}/revoke`),
        { method: "POST", body: {}, signal },
      ),
    transferCompanyOwner: (
      {
        targetMembershipId,
        expectedCurrentOwnerMembershipId,
        expectedCurrentOwnerUserId,
        formerOwnerPrimaryRole,
      },
      { signal } = {},
    ) => request(companyPath("/owner-transfer"), {
      method: "POST",
      body: {
        target_membership_id: targetMembershipId,
        expected_current_owner_membership_id: expectedCurrentOwnerMembershipId,
        expected_current_owner_user_id: expectedCurrentOwnerUserId,
        former_owner_primary_role: formerOwnerPrimaryRole,
      },
      signal,
    }),
    setMemberStatus: (membershipId, status, { signal } = {}) =>
      request(companyPath(`/members/${encodeURIComponent(membershipId)}/status`), {
        method: "PATCH",
        body: { status },
        signal,
      }),
    replaceMemberRoles: (
      membershipId,
      { roleIds, expectedRoleIds },
      { signal } = {},
    ) =>
      request(companyPath(`/members/${encodeURIComponent(membershipId)}/roles`), {
        method: "PUT",
        body: {
          role_ids: roleIds,
          expected_role_ids: expectedRoleIds,
        },
        signal,
      }),
    replaceMemberAccess: (
      membershipId,
      {
        roleIds,
        permissionOverrides,
        expectedRoleIds,
        expectedPermissionOverrides,
      },
      { signal } = {},
    ) =>
      request(companyPath(`/members/${encodeURIComponent(membershipId)}/access`), {
        method: "PUT",
        body: {
          role_ids: roleIds,
          permission_overrides: permissionOverrides,
          expected_role_ids: expectedRoleIds,
          expected_permission_overrides: expectedPermissionOverrides,
        },
        signal,
      }),
    replaceMemberPermissionOverrides: (
      membershipId,
      { permissionOverrides, expectedPermissionOverrides },
      { signal } = {},
    ) =>
      request(companyPath(`/members/${encodeURIComponent(membershipId)}/permissions`), {
        method: "PUT",
        body: {
          overrides: permissionOverrides,
          expected_overrides: expectedPermissionOverrides,
        },
        signal,
      }),
    listRoles: ({ signal } = {}) => request(companyPath("/roles"), { signal }),
    createRole: (
      { name, description = "", permissionCodes = [] },
      { signal } = {},
    ) =>
      request(companyPath("/roles"), {
        method: "POST",
        body: {
          name,
          description,
          permission_codes: permissionCodes,
        },
        signal,
      }),
    updateRole: (
      roleId,
      { name, description = "", permissionCodes = [] },
      { signal } = {},
    ) =>
      request(companyPath(`/roles/${encodeURIComponent(roleId)}`), {
        method: "PUT",
        body: {
          name,
          description,
          permission_codes: permissionCodes,
        },
        signal,
      }),
    deleteRole: (roleId, { signal } = {}) =>
      request(companyPath(`/roles/${encodeURIComponent(roleId)}`), {
        method: "DELETE",
        signal,
      }),
    listWallet: ({ signal } = {}) => request(companyPath("/wallet"), { signal }),
    listLedger: ({ signal } = {}) => request(companyPath("/ledger"), { signal }),
    listRecharges: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/wallet/recharges"), filters), { signal }),
    listModelGrants: ({ signal } = {}) =>
      request(companyPath("/model-grants"), { signal }),
    listResources: ({ signal } = {}) =>
      request(companyPath("/resources"), { signal }),
  };
}

export function createCompanyReportingApi(core) {
  const { request, companyPath, withQuery } = core;

  return {
    listDownloadRecords: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/download-records"), filters), { signal }),
    getTaskReport: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/reports/tasks"), filters), { signal }),
    getConsumptionReport: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/reports/consumption"), filters), { signal }),
    exportTaskReport: (filters = {}, { signal } = {}) =>
      request(withQuery(companyPath("/reports/tasks/export.csv"), filters), {
        signal,
        responseType: "text",
      }),
    exportConsumptionReport: (filters = {}, { signal } = {}) =>
      request(
        withQuery(companyPath("/reports/consumption/export.csv"), filters),
        { signal, responseType: "text" },
      ),
  };
}
