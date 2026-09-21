export function createManagementDrawerSubmitter(workspace) {
  const {
    client,
    data,
    demoMode,
    drawer,
  } = workspace.runtime;
  const {
    createCompanyInvitation,
    createPlatformCompany,
    reissueOwnerInvitation,
    submitPersonalPointGrant,
  } = workspace.actions;
  const {
    mergeData,
    mutate,
  } = workspace.orchestration;
  const {
    setDrawerError,
  } = workspace.state;
  const {
    billingPresentationState,
    money,
    permissionOverrideMap,
    readCapabilityEditor,
    requirePermissionCatalog,
    roleList,
    withMemberPermissionState,
  } = workspace.helpers;

  const submitDrawer = (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    let permissionCatalog = null;
    if (drawer.type === "roles" || drawer.type === "role") {
      try {
        permissionCatalog = requirePermissionCatalog(data.permissions);
      } catch (catalogError) {
        setDrawerError(catalogError.message);
        return;
      }
    }
    if (drawer.type === "invitation") {
      const payload = {
        email: form.get("email"),
        displayName: form.get("displayName"),
        primaryRole: form.get("primaryRole"),
        expiresInHours: Number(form.get("expiresInHours")),
        idempotencyKey: drawer.idempotencyKey,
      };
      void createCompanyInvitation(payload);
    } else if (drawer.type === "ownerTransfer") {
      const targetMembershipId = String(form.get("targetMembershipId") || "");
      const formerOwnerPrimaryRole = String(form.get("formerOwnerPrimaryRole") || "operator");
      const target = data.members.find((member) => member.membership_id === targetMembershipId);
      if (!target || target.status !== "active") {
        setDrawerError("请选择一位仍在职的组长或运营作为新老板。");
        return;
      }
      mutate(
        async () => {
          await client.transferCompanyOwner({
            targetMembershipId,
            expectedCurrentOwnerMembershipId: data.me.membership_id,
            expectedCurrentOwnerUserId: data.me.user_id,
            formerOwnerPrimaryRole,
          });
          mergeData({ me: await client.getCompanyMe() });
        },
        () => {
          const ownerRole = data.roles.find((role) => role.system_key === "owner");
          const formerRole = data.roles.find((role) => role.system_key === formerOwnerPrimaryRole);
          mergeData({
            me: { ...data.me, roles: [formerRole].filter(Boolean) },
            members: data.members.map((member) => {
              if (member.membership_id === targetMembershipId) return { ...member, roles: [ownerRole].filter(Boolean) };
              if (member.membership_id === data.me.membership_id) return { ...member, roles: [formerRole].filter(Boolean) };
              return member;
            }),
          });
        },
        "老板职责已交接；原老板已切换为所选公司级别",
      );
    } else if (drawer.type === "ownerInvitation") {
      const replacementEmail = String(form.get("replacementEmail") || "").trim();
      const replacementDisplayName = String(form.get("replacementDisplayName") || "").trim();
      if (!replacementEmail && replacementDisplayName) {
        setDrawerError("只有同时填写新的老板邮箱时才能修改老板姓名。");
        return;
      }
      void reissueOwnerInvitation(drawer.company, {
        replacementEmail,
        replacementDisplayName,
      });
    } else if (drawer.type === "roles") {
      const roleIds = [form.get("primaryRoleId"), ...form.getAll("customRoleIds")].filter(Boolean);
      const permissionOverrides = {};
      permissionCatalog.forEach(({ code }) => {
        const effect = form.get(`permission:${code}`);
        if (effect === "allow" || effect === "deny") permissionOverrides[code] = effect;
      });
      const member = drawer.member;
      mutate(
        () => client.replaceMemberAccess(member.membership_id, {
          roleIds,
          permissionOverrides,
          expectedRoleIds: roleList(member).map((role) => role.id),
          expectedPermissionOverrides: permissionOverrideMap(member),
        }),
        () =>
          mergeData({
            members: data.members.map((item) =>
              item.membership_id === member.membership_id
                ? withMemberPermissionState({
                    ...item,
                    roles: data.roles.filter((role) => roleIds.includes(role.id)),
                    permission_overrides: Object.entries(permissionOverrides).map(
                      ([permission_code, effect]) => ({ permission_code, effect }),
                    ),
                  }, data.roles)
                : item,
            ),
          }),
        "成员级别与个人权限已更新",
      );
    } else if (drawer.type === "role") {
      const payload = {
        name: form.get("name"),
        description: form.get("description"),
        permissionCodes: form.getAll("permissionCodes"),
      };
      const existing = drawer.role;
      const nextRoles = existing
        ? data.roles.map((role) => role.id === existing.id
          ? { ...role, name: payload.name, description: payload.description, permission_codes: payload.permissionCodes }
          : role)
        : [
            ...data.roles,
            { id: `role-${Date.now()}`, is_system: false, name: payload.name, description: payload.description, permission_codes: payload.permissionCodes },
          ];
      mutate(
        () => existing ? client.updateRole(existing.id, payload) : client.createRole(payload),
        () =>
          mergeData({
            roles: nextRoles,
            members: data.members.map((member) => withMemberPermissionState(member, nextRoles)),
          }),
        existing
          ? (existing.is_system ? "公司级别权限模板已更新" : "自定义角色已更新")
          : "自定义角色已创建",
      );
    } else if (drawer.type === "company") {
      const payload = {
        name: form.get("name"),
        ownerEmail: form.get("ownerEmail"),
        ownerDisplayName: form.get("ownerDisplayName"),
      };
      void createPlatformCompany(payload);
    } else if (drawer.type === "recharge") {
      if (billingPresentationState(drawer.summary || {}).kind !== "legacy_cents") {
        setDrawerError("积分入账必须关联可信支付或受控授权来源；旧人民币人工入账入口已关闭。" );
        return;
      }
      if (demoMode) {
        setDrawerError("演示模式不执行余额人工入账，也不会发起在线支付。");
        return;
      }
      const amountCents = Math.round(Number(form.get("amountYuan")) * 100);
      mutate(
        () =>
          client.rechargeAdminCompany(drawer.company.id, {
            amountCents,
            note: form.get("note"),
            idempotencyKey: drawer.idempotencyKey,
          }),
        null,
        `已为 ${drawer.company.name} 人工入账 ${money(amountCents)}`,
      );
    } else if (drawer.type === "personalPointsGrant") {
      void submitPersonalPointGrant(form);
    } else if (drawer.type === "model") {
      const existing = drawer.model;
      if (!existing) {
        setDrawerError("模型草稿由 Relay 自动生成，请刷新模型目录后再编辑。" );
        return;
      }
      let generationConfig;
      try {
        generationConfig = readCapabilityEditor(form);
      } catch (capabilityError) {
        setDrawerError(capabilityError?.message || "模型能力配置不完整。" );
        return;
      }
      const payload = {
        displayName: form.get("displayName"),
        providerKey: form.get("providerKey"),
        billingMode: form.get("billingMode"),
        capabilities: [{ key: "generation", config: generationConfig }],
      };
      mutate(
        () => client.updateAdminModel(existing.id, {
          displayName: payload.displayName,
          providerKey: payload.providerKey,
          billingMode: payload.billingMode,
          expectedCapabilityVersion: existing.capability_version,
          capabilities: payload.capabilities,
        }),
        () =>
          mergeData({
            adminModels: data.adminModels.map((model) => model.id === existing.id
              ? {
                  ...model,
                  display_name: payload.displayName,
                  provider_key: payload.providerKey,
                  billing_mode: payload.billingMode,
                  capability_version: Number(model.capability_version) + 1,
                  capabilities: { generation: generationConfig },
                  effective_capabilities: generationConfig,
                }
              : model),
          }),
        "模型能力版本已更新",
      );
    } else if (drawer.type === "resource") {
      const existing = drawer.resource;
      const payload = {
        key: existing?.key || form.get("key"),
        kind: existing?.kind || form.get("kind"),
        displayName: form.get("displayName"),
        description: form.get("description"),
        active: form.get("active") === "on",
      };
      mutate(
        () => existing
          ? client.updateAdminResource(existing.id, payload)
          : client.createAdminResource(payload),
        () =>
          mergeData({
            adminResources: existing
              ? data.adminResources.map((resource) => resource.id === existing.id
                ? {
                    ...resource,
                    display_name: payload.displayName,
                    description: payload.description,
                    active: payload.active,
                  }
                : resource)
              : [
                  ...data.adminResources,
                  { id: `res-${Date.now()}`, ...payload, display_name: payload.displayName },
                ],
          }),
        existing ? "功能资源已更新" : "功能资源已创建",
      );
    } else if (drawer.type === "channelCost") {
      const amountCents = Math.round(Number(form.get("amountYuan")) * 100);
      const occurredAtValue = new Date(form.get("occurredAt"));
      if (!Number.isFinite(amountCents)) {
        setDrawerError("请输入有效的渠道成本金额。" );
        return;
      }
      if (Number.isNaN(occurredAtValue.getTime())) {
        setDrawerError("请选择有效的成本发生时间。" );
        return;
      }
      const payload = {
        amountCents,
        idempotencyKey: drawer.idempotencyKey,
        channelKey: form.get("channelKey"),
        channelType: form.get("channelType"),
        occurredAt: occurredAtValue.toISOString(),
        externalReference: form.get("externalReference"),
        companyId: form.get("companyId"),
        taskId: form.get("taskId"),
        relayJobId: form.get("relayJobId"),
        note: form.get("note"),
      };
      mutate(
        () => client.createAdminChannelCost(payload),
        () => {
          const previousCosts = data.channelCosts || { total: 0, total_amount_cents: 0, items: [] };
          const channelCosts = [...(data.dashboard?.channel_costs || [])];
          const channelIndex = channelCosts.findIndex((item) => (
            item.channel_key === payload.channelKey && item.channel_type === payload.channelType
          ));
          if (channelIndex >= 0) {
            channelCosts[channelIndex] = {
              ...channelCosts[channelIndex],
              amount_cents: Number(channelCosts[channelIndex].amount_cents || 0) + amountCents,
            };
          } else {
            channelCosts.push({
              channel_key: payload.channelKey,
              channel_type: payload.channelType,
              amount_cents: amountCents,
            });
          }
          const nextCost = Number(data.dashboard?.channel_cost_cents || 0) + amountCents;
          const nextKnownGrossProfit =
            Number(data.dashboard?.platform_income_cents || 0) - nextCost;
          const financeComplete = data.dashboard?.finance_status === "complete";
          mergeData({
            channelCosts: {
              ...previousCosts,
              total: Number(previousCosts.total || 0) + 1,
              total_amount_cents: Number(previousCosts.total_amount_cents || 0) + amountCents,
              items: [
                {
                  id: `cost-${Date.now()}`,
                  amount_cents: amountCents,
                  channel_key: payload.channelKey,
                  channel_type: payload.channelType,
                  occurred_at: payload.occurredAt,
                  external_reference: payload.externalReference,
                  company_id: payload.companyId || null,
                  task_id: payload.taskId || null,
                  relay_job_id: payload.relayJobId || null,
                  note: payload.note,
                  source: "manual",
                },
                ...(previousCosts.items || []),
              ],
            },
            dashboard: {
              ...data.dashboard,
              channel_cost_cents: nextCost,
              known_gross_profit_cents: nextKnownGrossProfit,
              gross_profit_cents: financeComplete ? nextKnownGrossProfit : null,
              channel_costs: channelCosts,
            },
          });
        },
        `已录入 ${payload.channelKey} 渠道成本 ${money(amountCents)}`,
      );
    }
  };


  return submitDrawer;
}
