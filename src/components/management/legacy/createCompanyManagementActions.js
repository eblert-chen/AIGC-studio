export function createCompanyManagementActions(workspace) {
  const {
    client,
    data,
    demoMode,
    onSessionError,
  } = workspace.runtime;
  const {
    memberAccessRequestRef,
  } = workspace.requests;
  const {
    setBusy,
    setData,
    setDrawer,
    setDrawerError,
    setError,
    setMemberAccessLoading,
    setToast,
  } = workspace.state;
  const {
    mergeData,
    mutate,
  } = workspace.orchestration;
  const {
    copyableInvitationUrl,
    requirePermissionCatalog,
    roleList,
    withMemberPermissionState,
  } = workspace.helpers;

  const openMemberAccess = async (member) => {
    const returnFocusElement = globalThis.document?.activeElement ?? null;
    if (demoMode) {
      setDrawer({ type: "roles", member, returnFocusElement });
      return;
    }
    const requestId = memberAccessRequestRef.current + 1;
    memberAccessRequestRef.current = requestId;
    setMemberAccessLoading(true);
    setError("");
    try {
      const [members, roles, permissions] = await Promise.all([
        client.listMembers(),
        client.listRoles(),
        client.listPermissionCatalog(),
      ]);
      requirePermissionCatalog(permissions);
      const freshMember = members.find(
        (item) => item.membership_id === member.membership_id,
      );
      if (!freshMember) throw new Error("成员已不存在，请刷新后重试。");
      if (requestId !== memberAccessRequestRef.current) return;
      mergeData({ members, roles, permissions });
      setDrawer({
        type: "roles",
        member: withMemberPermissionState(freshMember, roles),
        returnFocusElement,
      });
    } catch (refreshError) {
      if (requestId === memberAccessRequestRef.current) {
        setError(refreshError?.message || "读取成员最新权限失败，请稍后重试。");
      }
    } finally {
      if (requestId === memberAccessRequestRef.current) setMemberAccessLoading(false);
    }
  };

  const openRoleEditor = (role = null) => {
    try {
      requirePermissionCatalog(data.permissions);
    } catch (catalogError) {
      setError(catalogError.message);
      return;
    }
    setDrawer(role ? { type: "role", role } : { type: "role" });
  };

  const setMemberStatus = (member) => {
    const nextStatus = member.status === "active" ? "disabled" : "active";
    if (nextStatus === "disabled" && !globalThis.confirm?.(`确认停用成员“${member.display_name}”？`)) return;
    mutate(
      () => client.setMemberStatus(member.membership_id, nextStatus),
      () =>
        mergeData({
          members: data.members.map((item) =>
            item.membership_id === member.membership_id ? { ...item, status: nextStatus } : item,
          ),
        }),
      nextStatus === "active" ? "成员已恢复" : "成员已停用，现有会话权限将被拒绝",
    );
  };

  const createCompanyInvitation = async (payload) => {
    setBusy(true);
    setDrawerError("");
    try {
      const result = demoMode
        ? {
            id: `invite-demo-${Date.now()}`,
            company_id: data.me?.company_id || "demo-company",
            email: payload.email,
            display_name: payload.displayName,
            primary_role: payload.primaryRole,
            status: "pending",
            expires_at: new Date(Date.now() + payload.expiresInHours * 3_600_000).toISOString(),
            created_at: new Date().toISOString(),
            invitation_url: null,
          }
        : await client.createInvitation(payload);
      setData((current) => ({
        ...current,
        invitations: {
          ...current.invitations,
          total: Math.max(current.invitations.total, current.invitations.items.length + 1),
          items: [
            result,
            ...current.invitations.items.filter((item) => item.id !== result.id),
          ],
        },
      }));
      setDrawer(null);
      setToast(demoMode
        ? "演示邀请已加入列表；演示模式不会生成真实链接"
        : result.invitation_url
          ? "邀请已创建；请复制仅显示一次的邀请链接"
          : "邀请已存在；如需新链接请重新发送");
    } catch (invitationError) {
      if (!onSessionError?.(invitationError)) {
        setDrawerError(invitationError?.message || "邀请创建失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const copyInvitationLink = async (invitation) => {
    const link = copyableInvitationUrl(invitation.invitation_url);
    if (!link) {
      setError("该一次性链接未保留或已不可用；请重新发送邀请以生成新链接。");
      return;
    }
    try {
      await globalThis.navigator?.clipboard?.writeText(link);
      setToast("一次性邀请链接已复制；请通过可信渠道发送");
    } catch {
      setError("浏览器未允许复制。请重新发送后再次尝试，页面不会直接展示邀请凭据。");
    }
  };

  const reissueCompanyInvitation = async (invitation) => {
    if (demoMode) {
      setToast("演示模式不会签发真实邀请链接");
      return;
    }
    if (!globalThis.confirm?.("重新发送会立即使旧邀请链接失效，是否继续？")) return;
    setBusy(true);
    setError("");
    try {
      const result = await client.reissueInvitation(invitation.id);
      setData((current) => ({
        ...current,
        invitations: {
          ...current.invitations,
          items: current.invitations.items.map((item) => item.id === result.id ? result : item),
        },
      }));
      setToast("新邀请已签发；请复制仅显示一次的新链接");
    } catch (invitationError) {
      if (!onSessionError?.(invitationError)) {
        setError(invitationError?.message || "邀请重新发送失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const revokeCompanyInvitation = async (invitation) => {
    if (!globalThis.confirm?.(`确认撤销发给 ${invitation.email} 的邀请？`)) return;
    setBusy(true);
    setError("");
    try {
      const result = demoMode
        ? { ...invitation, status: "revoked", invitation_url: null }
        : await client.revokeInvitation(invitation.id);
      setData((current) => ({
        ...current,
        invitations: {
          ...current.invitations,
          items: current.invitations.items.map((item) => item.id === result.id ? result : item),
        },
      }));
      setToast("邀请已撤销，旧链接不能再使用");
    } catch (invitationError) {
      if (!onSessionError?.(invitationError)) {
        setError(invitationError?.message || "邀请撤销失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const deleteCompanyRole = (role) => {
    if (!globalThis.confirm?.(`确认删除自定义角色“${role.name}”？已分配成员会同时失去这个附加角色。`)) return;
    const nextRoles = data.roles.filter((item) => item.id !== role.id);
    mutate(
      () => client.deleteRole(role.id),
      () => mergeData({
        roles: nextRoles,
        members: data.members.map((member) => withMemberPermissionState({
          ...member,
          roles: roleList(member).filter((assigned) => assigned.id !== role.id),
        }, nextRoles)),
      }),
      "自定义角色已删除",
    );
  };


  return {
    openMemberAccess,
    openRoleEditor,
    setMemberStatus,
    createCompanyInvitation,
    copyInvitationLink,
    reissueCompanyInvitation,
    revokeCompanyInvitation,
    deleteCompanyRole,
  };
}
