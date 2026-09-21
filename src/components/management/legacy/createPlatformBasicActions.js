export function createPlatformBasicActions(workspace) {
  const {
    client,
    data,
    demoMode,
    drawer,
    onSessionError,
    platformIdentity,
  } = workspace.runtime;
  const {
    companyControlRequestRef,
    personalPointGrantRequestRef,
  } = workspace.requests;
  const {
    setBusy,
    setData,
    setDrawer,
    setDrawerError,
    setDrawerState,
    setEntitlementBusyKey,
    setError,
    setOwnerInvitationLinks,
    setPaginationBusyKey,
    setToast,
  } = workspace.state;
  const {
    invalidateSensitiveData,
    load,
    mergeData,
    mutate,
  } = workspace.orchestration;
  const {
    canUsePlatformPermission,
    copyableInvitationUrl,
    makeOperationKey,
    mergePageRecords,
    personalPointBalanceLabel,
    personalPointGrantEligibilityError,
  } = workspace.helpers;
  const {
    DEMO_COMPANIES,
    MANAGEMENT_PAGE_SIZE,
    demoCompanyEntitlements,
    ownerInvitationLinks,
    paginationBusyKey,
  } = workspace.catalog;

  const createPlatformCompany = async (payload) => {
    setBusy(true);
    setDrawerError("");
    try {
      const result = demoMode
        ? {
            id: `co-demo-${Date.now()}`,
            name: payload.name,
            status: "active",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            owner_activation_required: false,
            owner_user_id: `usr-demo-${Date.now()}`,
            owner_membership_id: `mem-demo-${Date.now()}`,
          }
        : await client.createAdminCompany(payload);
      const invitationUrl = copyableInvitationUrl(result.owner_invitation_url);
      setData((current) => ({
        ...current,
        companies: {
          ...current.companies,
          total: current.companies.total + 1,
          items: [result, ...current.companies.items.filter((item) => item.id !== result.id)],
        },
      }));
      if (invitationUrl) {
        setOwnerInvitationLinks((current) => ({ ...current, [result.id]: invitationUrl }));
      }
      setDrawer(null);
      setToast(demoMode
        ? "演示企业已加入目录，不会创建真实老板账号"
        : result.owner_activation_required
          ? "企业已创建；老板账号需接受邀请后才会激活，请复制一次性链接"
          : "企业与老板账号已创建并可使用");
    } catch (companyError) {
      if (!onSessionError?.(companyError)) {
        setDrawerError(companyError?.message || "企业创建失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const copyOwnerInvitationLink = async (company) => {
    const link = copyableInvitationUrl(ownerInvitationLinks[company.id]);
    if (!link) {
      setError("该老板邀请链接未保留或已不可用；请重新签发后再复制。");
      return;
    }
    try {
      await globalThis.navigator?.clipboard?.writeText(link);
      setToast("老板一次性邀请链接已复制；请通过可信渠道发送");
    } catch {
      setError("浏览器未允许复制。可重新签发邀请，页面不会直接展示邀请凭据。");
    }
  };

  const reissueOwnerInvitation = async (company, {
    replacementEmail = "",
    replacementDisplayName = "",
  } = {}) => {
    if (demoMode) {
      setToast("演示模式不会签发真实老板邀请链接");
      return;
    }
    if (!company.owner_membership_id || !company.owner_user_id) {
      setDrawerError("服务端尚未返回老板成员快照，请关闭窗口并刷新企业目录后重试。");
      return;
    }
    setBusy(true);
    setDrawerError("");
    try {
      const result = await client.reissueAdminCompanyOwnerInvitation(company.id, {
        expectedOwnerMembershipId: company.owner_membership_id,
        expectedOwnerUserId: company.owner_user_id,
        replacementEmail,
        replacementDisplayName,
      });
      const invitationUrl = copyableInvitationUrl(result.invitation_url);
      if (!invitationUrl) throw new Error("服务端未返回可安全复制的老板邀请链接。");
      setOwnerInvitationLinks((current) => ({ ...current, [company.id]: invitationUrl }));
      setData((current) => ({
        ...current,
        companies: {
          ...current.companies,
          items: current.companies.items.map((item) => item.id === company.id
            ? {
                ...item,
                owner_activation_required: true,
                owner_membership_id: result.owner_membership_id,
                owner_user_id: result.owner_user_id,
                owner_invitation_expires_at: result.expires_at,
              }
            : item),
        },
      }));
      setDrawer(null);
      setToast("老板邀请已重新签发；请复制仅返回一次的新链接");
    } catch (invitationError) {
      if (onSessionError?.(invitationError)) return;
      if (invitationError?.status === 409) {
        await load("companies");
        setDrawerError("老板账号状态已变化，已刷新企业目录；请关闭窗口并确认最新状态后再操作。");
      } else {
        setDrawerError(invitationError?.message || "老板邀请重新签发失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const refreshPersonalPointGrantHistory = async (user) => {
    const requestId = personalPointGrantRequestRef.current + 1;
    personalPointGrantRequestRef.current = requestId;
    setDrawerState((current) => (
      current?.type === "personalPointsGrant" && current.user?.id === user.id
        ? { ...current, historyLoading: true, historyError: "" }
        : current
    ));
    try {
      const history = await client.listPersonalUserPointGrants(
        user.id,
        { page: 1, page_size: 20 },
      );
      if (requestId !== personalPointGrantRequestRef.current) return;
      setDrawerState((current) => (
        current?.type === "personalPointsGrant" && current.user?.id === user.id
          ? { ...current, history, historyLoading: false, historyError: "" }
          : current
      ));
    } catch (historyError) {
      if (requestId !== personalPointGrantRequestRef.current) return;
      if (onSessionError?.(historyError)) return;
      if (historyError?.status === 401) {
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
        return;
      }
      if (historyError?.status === 403) {
        invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
        return;
      }
      setDrawerState((current) => (
        current?.type === "personalPointsGrant" && current.user?.id === user.id
          ? {
              ...current,
              historyLoading: false,
              historyError: historyError?.message || "最近赠送记录读取失败，请稍后重试。",
            }
          : current
      ));
    }
  };

  const openPersonalPointGrant = (user) => {
    const eligibilityError = personalPointGrantEligibilityError(user);
    if (demoMode) {
      setError("演示模式不会创建真实个人积分账本记录。");
      return;
    }
    if (!platformIdentity?.is_platform_owner) {
      setError("只有平台所有者可以向个人用户赠送积分。");
      return;
    }
    if (eligibilityError) {
      setError(`${eligibilityError}。`);
      return;
    }
    setDrawer({
      type: "personalPointsGrant",
      user,
      idempotencyKey: makeOperationKey("personal-points-grant"),
      history: null,
      historyLoading: true,
      historyError: "",
      success: null,
    });
    void refreshPersonalPointGrantHistory(user);
  };

  const startNextPersonalPointGrant = () => {
    setDrawerError("");
    setDrawerState((current) => (
      current?.type === "personalPointsGrant"
        ? {
            ...current,
            idempotencyKey: makeOperationKey("personal-points-grant"),
            success: null,
          }
        : current
    ));
  };

  const submitPersonalPointGrant = async (form) => {
    const amountPoints = Number(form.get("amountPoints"));
    const note = String(form.get("note") || "").trim();
    if (
      !Number.isSafeInteger(amountPoints)
      || amountPoints <= 0
      || amountPoints > 9_000_000_000_000_000
    ) {
      setDrawerError("赠送数量必须是 1 到 9,000,000,000,000,000 之间的整数积分。");
      return;
    }
    if (!note) {
      setDrawerError("请填写赠送说明，便于后续审计和账本复核。");
      return;
    }
    if (note.length > 240) {
      setDrawerError("赠送说明不能超过 240 个字符。");
      return;
    }

    const activeDrawer = drawer;
    const targetUser = activeDrawer.user;
    setBusy(true);
    setDrawerError("");
    try {
      const result = await client.grantPersonalUserPoints(targetUser.id, {
        amountPoints,
        note,
        idempotencyKey: activeDrawer.idempotencyKey,
      });
      const wallet = result?.wallet || {};
      setData((current) => ({
        ...current,
        adminUsers: {
          ...current.adminUsers,
          items: current.adminUsers.items.map((item) => (
            item.id === targetUser.id
              ? {
                  ...item,
                  available_points: wallet.available_points ?? item.available_points,
                  reserved_points: wallet.reserved_points ?? item.reserved_points,
                }
              : item
          )),
        },
      }));
      setDrawerState((current) => (
        current?.type === "personalPointsGrant"
          && current.user?.id === targetUser.id
          && current.idempotencyKey === activeDrawer.idempotencyKey
          ? {
              ...current,
              user: {
                ...current.user,
                available_points: wallet.available_points ?? current.user.available_points,
                reserved_points: wallet.reserved_points ?? current.user.reserved_points,
              },
              success: {
                created: Boolean(result?.created),
                amountPoints,
                ledgerEntryId: result?.ledger_entry?.id || "",
              },
            }
          : current
      ));
      setToast(result?.created
        ? `已向 ${targetUser.display_name} 赠送 ${personalPointBalanceLabel(amountPoints)}`
        : "该请求已安全重放，系统未重复增加积分");
      void refreshPersonalPointGrantHistory(targetUser);
    } catch (grantError) {
      if (onSessionError?.(grantError)) return;
      if (grantError?.status === 409) {
        setDrawerError("同一操作凭据已用于不同的积分数量或说明。为避免重复入账，请关闭窗口后重新发起一笔赠送。");
      } else if (grantError?.status === 401) {
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
      } else if (grantError?.status === 403) {
        invalidateSensitiveData("赠送权限或近期强认证已失效，请重新核验身份后再试。");
      } else {
        setDrawerError(grantError?.message || "赠送积分失败；当前操作凭据会保留，可直接重试而不会重复入账。");
      }
    } finally {
      setBusy(false);
    }
  };

  const setGlobalUserStatus = (user) => {
    const targetStatus = user.status === "active" ? "suspended" : "active";
    if (!globalThis.confirm?.(
      targetStatus === "suspended"
        ? `确认暂停账号“${user.display_name}”？该账号的全部会话会立即撤销。`
        : `确认恢复账号“${user.display_name}”？恢复后仍需重新登录。`,
    )) return;
    mutate(
      () => client.setPlatformUserStatus(user.id, {
        expectedStatus: user.status,
        expectedAuthVersion: user.auth_version,
        targetStatus,
      }),
      () => mergeData({
        adminUsers: {
          ...data.adminUsers,
          items: data.adminUsers.items.map((item) => item.id === user.id
            ? { ...item, status: targetStatus, auth_version: item.auth_version + 1 }
            : item),
        },
      }),
      targetStatus === "active" ? "账号已恢复，可重新登录" : "账号已暂停，全部会话已撤销",
    );
  };

  const setCompanyStatus = (company) => {
    const nextStatus = company.status === "active" ? "suspended" : "active";
    if (nextStatus === "suspended" && !globalThis.confirm?.(`确认停用企业“${company.name}”？`)) return;
    mutate(
      () => client.setAdminCompanyStatus(company.id, nextStatus),
      () =>
        mergeData({
          companies: {
            ...data.companies,
            items: data.companies.items.map((item) =>
              item.id === company.id ? { ...item, status: nextStatus } : item,
            ),
          },
        }),
      nextStatus === "active" ? "企业已恢复" : "企业已停用",
    );
  };

  const companyDashboardRow = (companyId) => (
    (data.dashboard?.companies || []).find((item) => item.company_id === companyId)
  );

  const openCompanyControl = async (company, { preserveContent = false } = {}) => {
    const access = {
      entitlements: canUsePlatformPermission("platform.entitlements.read"),
      finance: canUsePlatformPermission("platform.finance.read"),
    };
    if (!access.entitlements && !access.finance) {
      setError("当前账号没有企业权益或财务读取权限。");
      return;
    }
    const requestId = companyControlRequestRef.current + 1;
    companyControlRequestRef.current = requestId;
    const previous = preserveContent && drawer?.type === "companyControl"
      && drawer.company?.id === company.id
      ? drawer
      : null;
    const returnFocusElement = previous?.returnFocusElement
      ?? globalThis.document?.activeElement
      ?? null;
    setDrawerError("");
    setDrawerState({
      type: "companyControl",
      company,
      returnFocusElement,
      access,
      loadingDomains: {
        entitlements: access.entitlements,
        finance: access.finance,
      },
      domainErrors: { entitlements: "", finance: "" },
      entitlements: previous?.entitlements || null,
      recharges: previous?.recharges || null,
      consumption: previous?.consumption || null,
      summary: companyDashboardRow(company.id) || previous?.summary || null,
    });

    if (demoMode) {
      const allConsumption = data.adminConsumption?.items || [];
      const items = allConsumption.filter((item) => item.company_id === company.id);
      const rechargeItems = company.id === DEMO_COMPANIES[0]?.id
        ? (data.recharges?.items || [])
        : [];
      setDrawerState((current) => current?.type === "companyControl"
        && current.company?.id === company.id
        ? {
            ...current,
            loadingDomains: { entitlements: false, finance: false },
            entitlements: access.entitlements ? demoCompanyEntitlements(company.id) : null,
            recharges: access.finance ? {
              page: 1,
              page_size: 50,
              total: rechargeItems.length,
              billing_unit: "POINT",
              billing_version: 2,
              total_amount_points: rechargeItems.reduce(
                (sum, item) => sum + Math.max(0, Number(item.amount_points || item.available_delta_points || 0)),
                0,
              ),
              items: rechargeItems,
            } : null,
            consumption: access.finance ? {
              page: 1,
              page_size: 50,
              total: items.length,
              billing_unit: "POINT",
              billing_version: 2,
              total_amount_points: items.reduce((sum, item) => sum + Number(item.amount_points || 0), 0),
              items,
            } : null,
          }
        : current);
      return;
    }

    const updateDomain = (domain, patch) => {
      if (requestId !== companyControlRequestRef.current) return;
      setDrawerState((current) => {
        if (current?.type !== "companyControl" || current.company?.id !== company.id) return current;
        const { domainError = "", ...domainPatch } = patch;
        return {
          ...current,
          ...domainPatch,
          loadingDomains: { ...current.loadingDomains, [domain]: false },
          domainErrors: { ...current.domainErrors, [domain]: domainError },
          summary: companyDashboardRow(company.id) || current.summary,
        };
      });
    };
    const handleDomainError = (domain, loadError, fallback) => {
      if (loadError?.status === 401) {
        companyControlRequestRef.current += 1;
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
        return;
      }
      if (loadError?.status === 403) {
        companyControlRequestRef.current += 1;
        invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
        return;
      }
      updateDomain(domain, { domainError: loadError?.message || fallback });
    };

    const jobs = [];
    if (access.entitlements) {
      jobs.push(
        client.getAdminCompanyEntitlements(company.id)
          .then((entitlements) => updateDomain("entitlements", {
            entitlements,
            domainError: "",
          }))
          .catch((loadError) => handleDomainError(
            "entitlements",
            loadError,
            "企业权益读取失败，请稍后重试。",
          )),
      );
    }
    if (access.finance) {
      jobs.push(
        Promise.all([
          client.listAdminCompanyRecharges(company.id, { page: 1, page_size: MANAGEMENT_PAGE_SIZE }),
          client.getAdminConsumptionReport({ company_id: company.id, page: 1, page_size: MANAGEMENT_PAGE_SIZE }),
        ])
          .then(([recharges, consumption]) => updateDomain("finance", {
            recharges,
            consumption,
            domainError: "",
          }))
          .catch((loadError) => handleDomainError(
            "finance",
            loadError,
            "企业账务读取失败，请稍后重试。",
          )),
      );
    }
    await Promise.all(jobs);
  };

  const loadMoreCompanyControl = async (kind) => {
    if (demoMode || drawer?.type !== "companyControl" || paginationBusyKey) return;
    const companyId = drawer.company.id;
    const busyKey = `company-control-${kind}`;
    const currentPage = kind === "recharges" ? drawer.recharges : drawer.consumption;
    const nextPage = Number(currentPage?.page || 1) + 1;
    setPaginationBusyKey(busyKey);
    setDrawerError("");
    try {
      const result = kind === "recharges"
        ? await client.listAdminCompanyRecharges(companyId, { page: nextPage, page_size: MANAGEMENT_PAGE_SIZE })
        : await client.getAdminConsumptionReport({ company_id: companyId, page: nextPage, page_size: MANAGEMENT_PAGE_SIZE });
      setDrawerState((current) => {
        if (current?.type !== "companyControl" || current.company.id !== companyId) return current;
        return {
          ...current,
          [kind]: mergePageRecords(
            current[kind],
            result,
            kind === "recharges" ? "id" : "ledger_entry_id",
          ),
        };
      });
    } catch (pageError) {
      if (pageError?.status === 401) {
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
      } else if (pageError?.status === 403) {
        invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
      } else {
        setDrawerError(pageError?.message || "企业账务下一页加载失败，请稍后重试。");
      }
    } finally {
      setPaginationBusyKey("");
    }
  };

  const updateEntitlementRow = (collection, identityKey, identity, patch) => {
    setDrawerState((current) => {
      if (current?.type !== "companyControl" || !current.entitlements) return current;
      return {
        ...current,
        entitlements: {
          ...current.entitlements,
          [collection]: (current.entitlements[collection] || []).map((item) => (
            item[identityKey] === identity ? { ...item, ...patch } : item
          )),
        },
      };
    });
  };

  const saveCompanyModelEntitlement = async (item, enabled, unitPricePoints) => {
    if (billingPresentationState(item).kind !== "points") {
      setDrawerError("该企业仍处于历史人民币计费，完成积分迁移后才能修改模型单价。" );
      return;
    }
    const price = Number(unitPricePoints);
    if (!Number.isSafeInteger(price) || price <= 0) {
      setDrawerError("模型单价必须是大于 0 的整数积分。" );
      return;
    }
    if (item.grant_id && !item.grant_updated_at) {
      setDrawerError("当前模型授权缺少并发版本。请点击“刷新可访问数据”读取最新状态后再操作；本次修改尚未提交。" );
      return;
    }
    const busyKey = `model:${item.model_id}`;
    setEntitlementBusyKey(busyKey);
    setDrawerError("");
    const payload = {
      model_id: item.model_id,
      enabled,
      price_per_second_points: item.billing_mode === "per_second" ? price : null,
      price_per_item_points: item.billing_mode === "per_item" ? price : null,
      config_override: item.config_override || {},
      expected_updated_at: item.grant_id ? item.grant_updated_at : null,
    };
    try {
      const result = demoMode
        ? {
            ...payload,
            grant_id: item.grant_id || `demo-grant-${Date.now()}`,
            updated_at: new Date().toISOString(),
          }
        : await client.upsertAdminModelGrant(drawer.company.id, payload);
      updateEntitlementRow("models", "model_id", item.model_id, {
        ...result,
        enabled,
        grant_id: result?.id || result?.grant_id || item.grant_id,
        grant_updated_at: result?.updated_at || result?.grant_updated_at || item.grant_updated_at,
      });
      setToast(enabled
        ? `${item.display_name} 的企业单价已保存并开通`
        : `${item.display_name} 已对该企业停用`);
    } catch (mutationError) {
      setDrawerError(mutationError?.status === 409
        ? "模型授权已被另一会话更新。请点击“刷新可访问数据”读取最新状态后再操作；本次修改没有覆盖服务端数据。"
        : (mutationError?.message || "模型权益更新失败，请稍后重试。"));
    } finally {
      setEntitlementBusyKey("");
    }
  };

  const saveCompanyResourceEntitlement = async (item, enabled) => {
    const busyKey = `resource:${item.resource_id}`;
    setEntitlementBusyKey(busyKey);
    setDrawerError("");
    try {
      const payload = { enabled, config_override: item.config_override || {} };
      const result = demoMode
        ? { ...payload, grant_id: item.grant_id || `demo-grant-${Date.now()}` }
        : await client.upsertAdminResourceGrant(
            drawer.company.id,
            item.resource_id,
            payload,
          );
      updateEntitlementRow("resources", "resource_id", item.resource_id, {
        ...result,
        enabled,
        grant_id: result?.id || result?.grant_id || item.grant_id,
      });
      setToast(enabled
        ? `${item.display_name} 已对该企业开通`
        : `${item.display_name} 已对该企业停用`);
    } catch (mutationError) {
      setDrawerError(mutationError?.message || "功能权益更新失败，请稍后重试。" );
    } finally {
      setEntitlementBusyKey("");
    }
  };

  const setModelState = (model, action) => {
    if (action === "disable" && !globalThis.confirm?.(`确认下线模型“${model.display_name}”？`)) return;
    mutate(
      () =>
        action === "publish"
          ? client.publishAdminModel(model.id)
          : client.disableAdminModel(model.id),
      () =>
        mergeData({
          adminModels: data.adminModels.map((item) =>
            item.id === model.id
              ? { ...item, status: action === "publish" ? "published" : "disabled", active: action === "publish" }
              : item,
          ),
        }),
      action === "publish" ? "模型已发布，可用于企业授权" : "模型已下线，新任务将不可使用",
    );
  };

  const approveRelayModelRevision = async (model, payload) => {
    setBusy(true);
    setError("");
    try {
      if (demoMode) throw new Error("演示模式不写入模型能力审批。");
      const result = await client.approveAdminRelayCapability(model.id, payload);
      await load("models");
      setToast(result?.changed === false
        ? "该 Relay 能力版本已确认，无需重复提交"
        : "Relay 能力审批已写入不可变历史");
      return result;
    } catch (approvalError) {
      onSessionError?.(approvalError);
      throw approvalError;
    } finally {
      setBusy(false);
    }
  };

  const syncRelayModelCandidate = async (model, payload) => {
    setBusy(true);
    setError("");
    try {
      if (demoMode) throw new Error("演示模式不写入 Relay 候选版本。");
      const result = await client.syncAdminRelayCapabilityCandidate(model.id, payload);
      await load("models");
      setToast(result?.changed === false
        ? "当前候选版本已经记录，无需重复同步"
        : "Relay 候选版本已记录，等待差异审批");
      return result;
    } catch (syncError) {
      onSessionError?.(syncError);
      throw syncError;
    } finally {
      setBusy(false);
    }
  };

  const reconcileRelayModels = async () => {
    if (demoMode || !canUsePlatformPermission("platform.models.manage")) return;
    setBusy(true);
    setError("");
    mergeData({
      relayModelReconcile: {
        status: "running",
        error: "",
        error_status: 0,
      },
    });
    try {
      const result = await client.reconcileAdminRelayModels();
      await load("models");
      mergeData({
        relayModelReconcile: {
          status: "success",
          error: "",
          error_status: 0,
        },
      });
      setToast(result?.changed === false
        ? "Relay 目录已对账，没有需要生成或更新的草稿"
        : "Relay 目录已重新对账，新的未发布草稿已写入 Platform");
    } catch (reconcileError) {
      if (!onSessionError?.(reconcileError)) {
        mergeData({
          relayModelReconcile: {
            status: "error",
            error: reconcileError?.message || "Relay 模型目录手动对账失败",
            error_status: Number(reconcileError?.status || 0),
          },
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const loadRelayCapabilityHistory = async (model) => {
    if (demoMode) throw new Error("演示模式没有真实审批历史。");
    return client.listAdminRelayCapabilityHistory(model.id);
  };

  const savePersonalModelGrant = async (grant, payload) => {
    setBusy(true);
    setError("");
    try {
      if (demoMode) throw new Error("演示模式不写入个人零售分发设置。");
      const result = await client.upsertAdminPersonalModelGrant(grant.model_id, payload);
      mergeData({
        personalModelGrants: {
          items: [
            ...(data.personalModelGrants?.items || []).filter((item) => item.model_id !== result.model_id),
            result,
          ].sort((left, right) => String(left.model_display_name || "").localeCompare(String(right.model_display_name || ""), "zh-CN")),
          error: "",
        },
      });
      setToast(result.enabled
        ? `${result.model_display_name} 已向个人工作区开通`
        : `${result.model_display_name} 已停止个人工作区新任务`);
      return result;
    } catch (grantError) {
      onSessionError?.(grantError);
      throw grantError;
    } finally {
      setBusy(false);
    }
  };

  const previewPersonalModelGrantBatch = async (changes) => {
    setBusy(true);
    setError("");
    try {
      if (demoMode) throw new Error("演示模式不生成个人零售分发快照。");
      return await client.previewAdminPersonalModelGrantBatch(changes);
    } catch (batchError) {
      onSessionError?.(batchError);
      throw batchError;
    } finally {
      setBusy(false);
    }
  };

  const savePersonalModelGrantBatch = async ({ changes, expectedSnapshot, reason, idempotencyKey }) => {
    setBusy(true);
    setError("");
    try {
      if (demoMode) throw new Error("演示模式不写入个人零售批量分发。");
      const result = await client.executeAdminPersonalModelGrantBatch({
        changes,
        expectedSnapshot,
        reason,
        idempotencyKey,
      });
      await load("models");
      setToast(`个人零售批次已完成：${result.applied_cell_count || 0} 个模型发生变更`);
      return result;
    } catch (batchError) {
      onSessionError?.(batchError);
      throw batchError;
    } finally {
      setBusy(false);
    }
  };


  return {
    createPlatformCompany,
    copyOwnerInvitationLink,
    reissueOwnerInvitation,
    refreshPersonalPointGrantHistory,
    openPersonalPointGrant,
    startNextPersonalPointGrant,
    submitPersonalPointGrant,
    setGlobalUserStatus,
    setCompanyStatus,
    companyDashboardRow,
    openCompanyControl,
    loadMoreCompanyControl,
    updateEntitlementRow,
    saveCompanyModelEntitlement,
    saveCompanyResourceEntitlement,
    setModelState,
    approveRelayModelRevision,
    syncRelayModelCandidate,
    reconcileRelayModels,
    loadRelayCapabilityHistory,
    savePersonalModelGrant,
    previewPersonalModelGrantBatch,
    savePersonalModelGrantBatch,
  };
}
