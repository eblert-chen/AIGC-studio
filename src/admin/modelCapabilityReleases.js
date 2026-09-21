const COMPATIBLE_STATUSES = new Set(["identical", "compatible_restriction"]);
const KNOWN_APPROVAL_STATUSES = new Set(["approved", "pending", "not_required"]);
const ROUTE_EVIDENCE_STATUSES = new Set([
  "ready",
  "blocked",
  "missing",
  "revision_drift",
  "unavailable",
]);

const CHANGE_KIND_LABELS = Object.freeze({
  added: "新增",
  removed: "移除",
  changed: "调整",
});

const DIFF_CLASSIFICATION_LABELS = Object.freeze({
  unavailable: "差异待服务端计算",
  initial: "首次能力声明",
  unchanged: "能力未变化",
  restriction: "能力收紧",
  expansion: "能力扩展",
  mixed: "混合变更",
});

const RELEASE_STATE_LABELS = Object.freeze({
  approved: "能力已验收",
  pending: "等待能力审批",
  ready: "无需重复审批",
  blocked: "能力越界",
  collision: "能力修订冲突",
  unverified: "Relay 状态待核验",
  needs_config: "待配置 Platform 能力",
  unmapped: "待建立 Platform 映射",
  platform_only: "Relay 无映射",
});

const RELEASE_STATE_TONES = Object.freeze({
  approved: "active",
  pending: "draft",
  ready: "active",
  blocked: "failed",
  collision: "failed",
  unverified: "disabled",
  needs_config: "draft",
  unmapped: "draft",
  platform_only: "disabled",
});

function text(value) {
  return value == null ? "" : String(value).trim();
}

function bool(value, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

function asItems(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.items)) return value.items;
  return [];
}

export function shortRevision(value) {
  const revision = text(value);
  if (!revision) return "—";
  return revision.length > 18 ? `${revision.slice(0, 15)}…` : revision;
}

export function capabilityReleasePresentation(state) {
  const safeState = Object.hasOwn(RELEASE_STATE_LABELS, state)
    ? state
    : "unverified";
  return {
    state: safeState,
    label: RELEASE_STATE_LABELS[safeState],
    tone: RELEASE_STATE_TONES[safeState],
  };
}

export function normalizeCapabilityDiff(value) {
  const source = value && typeof value === "object" ? value : {};
  const classification = Object.hasOwn(DIFF_CLASSIFICATION_LABELS, source.classification)
    ? source.classification
    : "unavailable";
  const changes = asItems(source.changes)
    .filter((change) => change && typeof change === "object")
    .map((change, index) => ({
      id: text(change.id) || `${text(change.path) || "capability"}-${index}`,
      path: text(change.path) || "capability",
      kind: Object.hasOwn(CHANGE_KIND_LABELS, change.kind) ? change.kind : "changed",
      before: change.before ?? null,
      after: change.after ?? null,
    }));
  return {
    classification,
    label: DIFF_CLASSIFICATION_LABELS[classification],
    changes,
  };
}

export function normalizeCapabilityHistory(value) {
  return asItems(value)
    .filter((entry) => entry && typeof entry === "object")
    .map((entry, index) => ({
      id: text(entry.id) || text(entry.request_id) || `history-${index}`,
      eventType: text(entry.event_type) || "approval",
      actorUserId: text(entry.actor_user_id),
      actorKind: text(entry.actor_kind) || (entry.actor_user_id ? "user" : "system"),
      actorKey: text(entry.actor_key),
      reason: text(entry.reason),
      beforeRevision: text(entry.before_revision),
      afterRevision: text(entry.after_revision),
      catalogRevision: text(entry.catalog_revision),
      requestId: text(entry.request_id),
      createdAt: text(entry.created_at),
      capabilityDiff: normalizeCapabilityDiff(entry.capability_diff),
    }));
}

export function normalizeRelayCapabilityAudit(payload = {}) {
  const source = payload && typeof payload === "object" ? payload : {};
  return {
    catalogRevision: text(source.catalog_revision),
    etag: text(source.etag),
    error: text(source.error),
    errorStatus: Number(source.error_status || source.status || 0),
    platformOnlyModelIds: asItems(source.platform_only_model_ids).map(text).filter(Boolean),
    items: asItems(source.items).map((item) => {
      const candidateRevision = text(item?.candidate_revision || item?.capability_revision);
      const approvedRevision = text(item?.approved_revision);
      const compatible = COMPATIBLE_STATUSES.has(item?.status);
      const inferredRequiresApproval = Boolean(
        item?.platform_model_id
        && compatible
        && candidateRevision
        && candidateRevision !== approvedRevision,
      );
      const requiresApproval = bool(
        item?.requires_approval,
        bool(item?.approval_required, inferredRequiresApproval),
      );
      const routeEvidenceStatus = ROUTE_EVIDENCE_STATUSES.has(item?.route_evidence_status)
        ? item.route_evidence_status
        : "unavailable";
      return {
        ...item,
        relayModelId: text(item?.relay_model_id),
        platformModelId: text(item?.platform_model_id),
        candidateRevision,
        approvedRevision,
        requiresApproval,
        approvalStatus: text(item?.approval_status) || (
          approvedRevision && approvedRevision === candidateRevision
            ? "approved"
            : requiresApproval ? "pending" : "not_required"
        ),
        capabilityDiff: normalizeCapabilityDiff(item?.capability_diff),
        approvalHistory: normalizeCapabilityHistory(item?.approval_history),
        routeEvidenceStatus,
        routeEvidenceBlockers: asItems(item?.route_evidence_blockers).map(text).filter(Boolean),
        routingReleaseSha256: text(item?.routing_release_sha256),
        modelReleaseId: text(item?.model_release_id),
        modelReleaseRevision: text(item?.model_release_revision),
        routeCount: Number.isInteger(Number(item?.route_count)) ? Number(item.route_count) : null,
        enabledRouteCount: Number.isInteger(Number(item?.enabled_route_count)) ? Number(item.enabled_route_count) : null,
        acceptedRouteCount: Number.isInteger(Number(item?.accepted_route_count)) ? Number(item.accepted_route_count) : null,
        freshTestCount: Number.isInteger(Number(item?.fresh_test_count)) ? Number(item.fresh_test_count) : null,
        latestSuccessfulTestAt: text(item?.latest_successful_test_at),
        routes: asItems(item?.routes).map((route) => ({
          routeId: text(route?.route_id),
          channelId: Number.isInteger(Number(route?.channel_id)) ? Number(route.channel_id) : null,
          upstreamModel: text(route?.upstream_model),
          adapterProfileId: text(route?.adapter_profile_id),
          adapterProfileRevision: text(route?.adapter_profile_revision),
          enabled: route?.enabled === true,
          accepted: route?.accepted === true,
          fresh: route?.fresh === true,
          requiredTestModes: asItems(route?.required_test_modes).map(text).filter(Boolean),
          freshTestModes: asItems(route?.fresh_test_modes).map(text).filter(Boolean),
        })).filter((route) => route.routeId && route.channelId && route.upstreamModel),
        evidenceGeneratedAt: text(item?.evidence_generated_at),
        testFreshnessMaxAgeSeconds: Number.isInteger(Number(item?.test_freshness_max_age_seconds))
          ? Number(item.test_freshness_max_age_seconds)
          : null,
      };
    }),
  };
}

export function capabilityReleaseState(model, relay) {
  if (!model && relay) return "unmapped";
  if (model && !relay) return "platform_only";
  if (
    relay?.status === "revision_collision"
    || relay?.approvalStatus === "revision_collision"
  ) return "collision";
  if (relay?.status === "unsafe_expansion") return "blocked";
  if (relay?.status === "platform_unconfigured") return "needs_config";
  if (!COMPATIBLE_STATUSES.has(relay?.status)) return "unverified";
  if (!KNOWN_APPROVAL_STATUSES.has(relay?.approvalStatus)) return "unverified";
  if (
    relay?.approvalStatus === "approved"
    && relay.candidateRevision
    && relay?.approvedRevision
    && relay.approvedRevision === relay.candidateRevision
  ) return "approved";
  if (relay?.requiresApproval && relay.approvalStatus === "pending") return "pending";
  if (!relay?.requiresApproval && relay.approvalStatus === "not_required") return "ready";
  return "unverified";
}

export function buildModelCapabilityReleaseRows(adminModels = [], relayPayload = {}) {
  const audit = normalizeRelayCapabilityAudit(relayPayload);
  const models = Array.isArray(adminModels) ? adminModels : [];
  const modelsById = new Map(models.map((model) => [text(model?.id), model]));
  const rows = [];
  const attachedModelIds = new Set();

  audit.items.forEach((relay) => {
    const model = modelsById.get(relay.platformModelId) || null;
    if (model) attachedModelIds.add(text(model.id));
    const releaseState = capabilityReleaseState(model, relay);
    rows.push({
      key: model ? `platform:${model.id}` : `relay:${relay.relayModelId}`,
      model,
      relay,
      releaseState,
      releaseStateLabel: capabilityReleasePresentation(releaseState).label,
      canApprove: Boolean(
        model
        && relay.requiresApproval
        && COMPATIBLE_STATUSES.has(relay.status)
        && relay.approvalStatus === "pending"
        && relay.candidateRevision
        && relay.routeEvidenceStatus === "ready"
      ),
      canPublish: Boolean(
        model
        && releaseState === "approved"
        && relay.routeEvidenceStatus === "ready",
      ),
      productionReady: Boolean(
        model
        && model.status === "published"
        && releaseState === "approved"
        && relay.routeEvidenceStatus === "ready"
      ),
    });
  });

  models.forEach((model) => {
    if (attachedModelIds.has(text(model?.id))) return;
    const releaseState = "platform_only";
    rows.push({
      key: `platform:${model.id}`,
      model,
      relay: null,
      releaseState,
      releaseStateLabel: capabilityReleasePresentation(releaseState).label,
      canApprove: false,
      canPublish: false,
      productionReady: false,
    });
  });

  return {
    ...audit,
    rows,
    counts: rows.reduce((result, row) => {
      result.total += 1;
      result[row.releaseState] = (result[row.releaseState] || 0) + 1;
      if (row.productionReady) result.productionReady += 1;
      return result;
    }, { total: 0, productionReady: 0 }),
  };
}

export function buildCapabilityApprovalPayload({
  model,
  relay,
  catalogRevision = "",
  reason,
  requireRouteEvidence = true,
}) {
  const normalizedReason = text(reason);
  const reasonLength = Array.from(normalizedReason).length;
  if (!model?.id || !Number.isInteger(Number(model.capability_version))) {
    throw new Error("模型能力版本缺失，请刷新目录后重试。");
  }
  if (!relay?.candidateRevision) {
    throw new Error("Relay 候选版本缺失，不能提交审批。");
  }
  if (requireRouteEvidence && relay?.routeEvidenceStatus !== "ready") {
    throw new Error(
      relay?.routeEvidenceBlockers?.[0]
        || "当前模型缺少与路由、渠道及凭据版本精确绑定的新鲜成功测试。",
    );
  }
  if (requireRouteEvidence && !text(relay?.routingReleaseSha256)) {
    throw new Error("Relay 路由发布修订缺失，请刷新证据后重试。");
  }
  if (reasonLength < 3) {
    throw new Error("审批原因至少需要 3 个字符，说明本次能力变更的业务依据。");
  }
  if (reasonLength > 500) {
    throw new Error("审批原因不能超过 500 个字符。");
  }
  return {
    expectedCapabilityVersion: Number(model.capability_version),
    expectedCatalogRevision: text(catalogRevision) || undefined,
    expectedCapabilityRevision: relay.candidateRevision,
    expectedRoutingReleaseSha256: requireRouteEvidence
      ? text(relay?.routingReleaseSha256) || undefined
      : undefined,
    reason: normalizedReason,
  };
}

export function capabilityChangeLabel(change) {
  return CHANGE_KIND_LABELS[change?.kind] || CHANGE_KIND_LABELS.changed;
}

export function serializeCapabilityValue(value) {
  if (value == null || value === "") return "无";
  if (typeof value === "boolean") return value ? "支持" : "不支持";
  if (Array.isArray(value)) return value.length ? value.join("、") : "空集合";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const PERSONAL_GRANT_BLOCKER_LABELS = Object.freeze({
  model_unpublished: "模型尚未发布",
  relay_unapproved: "尚未批准 Relay 能力版本",
  capability_empty: "没有可分发的有效能力",
  route_evidence_not_ready: "Relay 精确路由测试证据未就绪",
  price_missing: "积分价格尚未配置",
});

function capabilityModes(value) {
  const modes = value?.modes;
  return modes && typeof modes === "object" && !Array.isArray(modes) ? modes : {};
}

export function normalizePersonalModelGrantCatalog(value, adminModels = [], relayPayload = {}) {
  const source = Array.isArray(value) ? { items: value } : (value && typeof value === "object" ? value : {});
  const modelsById = new Map(
    (Array.isArray(adminModels) ? adminModels : [])
      .filter((model) => model?.id)
      .map((model) => [text(model.id), model]),
  );
  const relayByPlatformModelId = new Map(
    normalizeRelayCapabilityAudit(relayPayload).items
      .filter((item) => item.platformModelId)
      .map((item) => [item.platformModelId, item]),
  );
  const items = asItems(source.items).map((grant) => {
    const model = modelsById.get(text(grant?.model_id)) || null;
    const relay = relayByPlatformModelId.get(text(grant?.model_id)) || null;
    const billingMode = text(model?.billing_mode) || (
      grant?.price_per_second_points != null ? "per_second" : "per_item"
    );
    const pricePoints = billingMode === "per_second"
      ? grant?.price_per_second_points
      : grant?.price_per_item_points;
    const blockers = [];
    if (text(grant?.model_status) !== "published") blockers.push("model_unpublished");
    if (!text(grant?.relay_capability_revision)) blockers.push("relay_unapproved");
    if (!Object.keys(capabilityModes(grant?.effective_capabilities)).length) blockers.push("capability_empty");
    if (relay?.routeEvidenceStatus !== "ready") blockers.push("route_evidence_not_ready");
    if (!Number.isInteger(Number(pricePoints)) || Number(pricePoints) < 1) blockers.push("price_missing");
    return {
      ...grant,
      model: model || {
        id: text(grant?.model_id),
        slug: text(grant?.model_slug),
        display_name: text(grant?.model_display_name),
        status: text(grant?.model_status),
        capability_version: Number(grant?.capability_version || 0),
      },
      billingMode,
      pricePoints: Number.isInteger(Number(pricePoints)) && Number(pricePoints) > 0
        ? Number(pricePoints)
        : null,
      blockers,
      blockerLabels: blockers.map((blocker) => PERSONAL_GRANT_BLOCKER_LABELS[blocker]),
      canEnable: blockers.every((blocker) => blocker === "price_missing"),
      capabilitySource: text(grant?.relay_capability_revision)
        ? relay?.routeEvidenceStatus === "ready"
          ? "Relay 能力与精确路由证据已就绪"
          : "Relay 能力已批准，路由证据仍阻塞"
        : "尚无 Relay 批准证据",
    };
  });
  return {
    items,
    error: text(source.error),
    errorStatus: Number(source.error_status || source.status || 0),
  };
}

export function buildPersonalModelGrantPayload({ grant, enabled, pricePoints, reason }) {
  const modelId = text(grant?.model_id || grant?.model?.id);
  const capabilityVersion = Number(grant?.capability_version || grant?.model?.capability_version);
  const billingMode = text(grant?.billingMode || grant?.model?.billing_mode);
  const normalizedPrice = Number(pricePoints);
  const normalizedReason = text(reason);
  const reasonLength = Array.from(normalizedReason).length;
  if (!modelId || !Number.isInteger(capabilityVersion) || capabilityVersion < 1) {
    throw new Error("模型能力版本缺失，请刷新个人零售目录后重试。");
  }
  if (!new Set(["per_second", "per_item"]).has(billingMode)) {
    throw new Error("模型计费方式未知，不能配置个人积分价格。");
  }
  if (!Number.isInteger(normalizedPrice) || normalizedPrice < 1) {
    throw new Error("积分价格必须是大于 0 的整数。");
  }
  if (reasonLength < 3) {
    throw new Error("请填写至少 3 个字符的变更原因。");
  }
  if (reasonLength > 500) {
    throw new Error("变更原因不能超过 500 个字符。");
  }
  return {
    expectedCapabilityVersion: capabilityVersion,
    expectedQuoteRevision: text(grant?.quote_revision) || null,
    enabled: Boolean(enabled),
    pricePerSecondPoints: billingMode === "per_second" ? normalizedPrice : null,
    pricePerItemPoints: billingMode === "per_item" ? normalizedPrice : null,
    configOverride: grant?.config_override && typeof grant.config_override === "object"
      ? grant.config_override
      : {},
    reason: normalizedReason,
  };
}

export function buildPersonalModelGrantBatchChange({ grant, enabled, pricePoints }) {
  const payload = buildPersonalModelGrantPayload({
    grant,
    enabled,
    pricePoints,
    reason: "批量分发预览",
  });
  return {
    model_id: text(grant?.model_id || grant?.model?.id),
    expected_capability_version: payload.expectedCapabilityVersion,
    expected_quote_revision: payload.expectedQuoteRevision,
    enabled: payload.enabled,
    price_per_second_points: payload.pricePerSecondPoints,
    price_per_item_points: payload.pricePerItemPoints,
    config_override: payload.configOverride,
  };
}
