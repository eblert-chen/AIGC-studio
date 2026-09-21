import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  Plus,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  billingAmountLabel,
  billingPresentationState,
  billingTotalsLabel,
  billingUnitLabel,
} from "../../billingPresentation.js";
import {
  CollectionState,
  EmptyRows,
  PageLoadMore,
  QuietButton,
  STATUS_LABELS,
  StatusPill,
} from "./ManagementPrimitives.jsx";
import {
  RESOURCE_KIND_LABELS,
  pricingModeLabel,
  shortDate,
} from "./managementPresentation.js";
import { pricingQuantityLabel } from "./managementConsoleSupport.js";

export function CompanyEntitlementsPanel({
  drawer,
  drawerError,
  busyKey,
  onReload,
  onLoadMore,
  onSaveModel,
  onToggleResource,
  onRecharge,
  onClose,
  canManageEntitlements = true,
  canRecharge = true,
  paginationBusyKey = "",
}) {
  const access = drawer.access || { entitlements: true, finance: true };
  const availableTabs = [
    ...(access.entitlements ? [
      { id: "models", label: "模型授权" },
      { id: "resources", label: "功能权益" },
    ] : []),
    ...(access.finance ? [{ id: "finance", label: "用量与账务" }] : []),
  ];
  const [tab, setTab] = useState(availableTabs[0]?.id || "models");
  const tabRefs = useRef([]);
  const models = drawer.entitlements?.models || [];
  const resources = drawer.entitlements?.resources || [];
  const [priceDrafts, setPriceDrafts] = useState({});

  useEffect(() => {
    const next = {};
    models.forEach((item) => {
      const points = item.billing_mode === "per_item"
        ? item.price_per_item_points
        : item.price_per_second_points;
      next[item.model_id] = billingPresentationState(item).kind === "points" && points != null
        ? String(points)
        : "";
    });
    setPriceDrafts(next);
  }, [drawer.company?.id, drawer.entitlements]);

  const resourceGroups = useMemo(() => resources.reduce((result, resource) => {
    const kind = resource.kind || "feature";
    if (!result[kind]) result[kind] = [];
    result[kind].push(resource);
    return result;
  }, {}), [resources]);
  const resourceKinds = Object.keys(resourceGroups).sort((left, right) => (
    (RESOURCE_KIND_LABELS[left] || left).localeCompare(RESOURCE_KIND_LABELS[right] || right, "zh-CN")
  ));
  const enabledModels = models.filter((item) => (
    item.enabled && item.status === "published"
  )).length;
  const enabledResources = resources.filter((item) => (
    item.enabled && item.active
  )).length;
  const summary = drawer.summary || {};
  const financeBilling = billingPresentationState(summary);
  const legacyManualRechargeAvailable = financeBilling.kind === "legacy_cents";

  useEffect(() => {
    if (availableTabs.some((item) => item.id === tab)) return;
    setTab(availableTabs[0]?.id || "models");
  }, [access.entitlements, access.finance, tab]);

  const selectTabByKeyboard = (event, index) => {
    let nextIndex = index;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % availableTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (index - 1 + availableTabs.length) % availableTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = availableTabs.length - 1;
    else return;
    event.preventDefault();
    const nextTab = availableTabs[nextIndex];
    setTab(nextTab.id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div className="control-company-panel">
      <div className="control-company-panel-tabs" role="tablist" aria-label="企业配置分类">
        {availableTabs.map((item, index) => {
          const count = item.id === "models"
            ? `${enabledModels}/${models.length}`
            : item.id === "resources"
              ? `${enabledResources}/${resources.length}`
              : drawer.consumption?.total || 0;
          return (
            <button
              key={item.id}
              ref={(element) => { tabRefs.current[index] = element; }}
              id={`company-control-tab-${item.id}`}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              aria-controls={`company-control-panel-${item.id}`}
              tabIndex={tab === item.id ? 0 : -1}
              className={tab === item.id ? "is-active" : ""}
              onClick={() => setTab(item.id)}
              onKeyDown={(event) => selectTabByKeyboard(event, index)}
            >
              {item.label} <span>{count}</span>
            </button>
          );
        })}
      </div>

      <div className="control-company-panel-body">
        {drawerError && <div className="control-drawer-error" role="alert"><WarningCircle size={18} weight="fill" /><span>{drawerError}</span></div>}

        {tab === "models" && (
          <section className="control-entitlement-section" id="company-control-panel-models" role="tabpanel" aria-labelledby="company-control-tab-models">
            <header><div><h3>模型与企业积分价</h3><p>目录计费方式固定；企业积分价使用整数，历史人民币授权只读。</p></div><span>{models.length} 个模型</span></header>
            {drawer.loadingDomains?.entitlements ? (
              <CollectionState state="loading" title="正在读取模型授权" detail="财务页可独立使用，不会等待此请求。" />
            ) : drawer.domainErrors?.entitlements ? (
              <CollectionState state="error" title="模型授权读取失败" detail={drawer.domainErrors.entitlements} onRetry={onReload} />
            ) : <div className="control-entitlement-list is-models">
              {models.map((item) => {
                const billing = billingPresentationState(item);
                const points = item.billing_mode === "per_item"
                  ? item.price_per_item_points
                  : item.price_per_second_points;
                const draft = priceDrafts[item.model_id] ?? "";
                const draftPoints = Number(draft);
                const priceValid = draft !== "" && Number.isSafeInteger(draftPoints) && draftPoints > 0;
                const priceChanged = priceValid && draftPoints !== Number(points || 0);
                const pending = busyKey === `model:${item.model_id}`;
                const canEditPointPrice = billing.kind === "points";
                const canEnable = item.status === "published" && priceValid && canEditPointPrice;
                return (
                  <article key={item.model_id}>
                    <div className="control-entitlement-copy"><strong>{item.display_name}</strong><small>{item.slug}</small><span>{pricingModeLabel(item.billing_mode)}计费 · {billingUnitLabel(item)} · 目录{STATUS_LABELS[item.status] || item.status}</span></div>
                    <label><span>{item.billing_mode === "per_item" ? "每条积分价" : "每秒积分价"}</span><input type="number" inputMode="numeric" min="1" step="1" value={draft} onChange={(event) => setPriceDrafts((current) => ({ ...current, [item.model_id]: event.target.value }))} disabled={!canManageEntitlements || !canEditPointPrice || pending || (item.status !== "published" && !item.enabled)} placeholder={canEditPointPrice ? "输入整数积分" : "完成积分迁移后可编辑"} /></label>
                    <StatusPill
                      value={item.enabled && item.status === "published" ? "active" : "disabled"}
                      label={item.enabled
                        ? (item.status === "published" ? "已开通" : "历史授权")
                        : "未开通"}
                    />
                    <div className="control-entitlement-actions">
                      {item.enabled ? (
                        <>
                          <button type="button" disabled={!canManageEntitlements || !canEditPointPrice || pending || item.status !== "published" || !priceChanged} onClick={() => onSaveModel(item, true, draftPoints)}>{pending ? <SpinnerGap size={14} className="spin" /> : null}保存积分价</button>
                          <button type="button" className="is-danger" disabled={!canManageEntitlements || !canEditPointPrice || pending || !priceValid} onClick={() => onSaveModel(item, false, draftPoints)}>停用</button>
                        </>
                      ) : (
                        <button type="button" disabled={!canManageEntitlements || pending || !canEnable} onClick={() => onSaveModel(item, true, draftPoints)}>{pending ? <SpinnerGap size={14} className="spin" /> : null}开通</button>
                      )}
                    </div>
                  </article>
                );
              })}
              {!models.length && <p className="control-empty-block">模型目录为空，请先在模型目录创建并发布模型。</p>}
            </div>}
          </section>
        )}

        {tab === "resources" && (
          <section className="control-entitlement-section" id="company-control-panel-resources" role="tabpanel" aria-labelledby="company-control-tab-resources">
            <header><div><h3>功能、智能体与外部 API</h3><p>列表完全来自服务端资源目录，可逐项为当前企业启停。</p></div><span>{resources.length} 项资源</span></header>
            {drawer.loadingDomains?.entitlements ? (
              <CollectionState state="loading" title="正在读取功能权益" detail="财务页可独立使用，不会等待此请求。" />
            ) : drawer.domainErrors?.entitlements ? (
              <CollectionState state="error" title="功能权益读取失败" detail={drawer.domainErrors.entitlements} onRetry={onReload} />
            ) : <>{resourceKinds.map((kind) => (
              <div className="control-entitlement-resource-group" key={kind}>
                <header><strong>{RESOURCE_KIND_LABELS[kind] || kind}</strong><span>{resourceGroups[kind].filter((item) => item.enabled && item.active).length}/{resourceGroups[kind].length} 已开通</span></header>
                <div className="control-entitlement-list is-resources">
                  {resourceGroups[kind].map((item) => {
                    const pending = busyKey === `resource:${item.resource_id}`;
                    return (
                      <article key={item.resource_id}>
                        <div className="control-entitlement-copy"><strong>{item.display_name}</strong><small>{item.key}</small>{!item.active && <span>目录已停用，只能关闭现有权益</span>}</div>
                        <StatusPill
                          value={item.enabled && item.active ? "active" : "disabled"}
                          label={item.enabled
                            ? (item.active ? "已开通" : "历史授权")
                            : "未开通"}
                        />
                        <button type="button" className={item.enabled ? "is-danger" : ""} disabled={!canManageEntitlements || pending || (!item.active && !item.enabled)} onClick={() => onToggleResource(item, !item.enabled)}>{pending ? <SpinnerGap size={14} className="spin" /> : null}{item.enabled ? "停用" : "开通"}</button>
                      </article>
                    );
                  })}
                </div>
              </div>
            ))}
            {!resourceKinds.length && <p className="control-empty-block">资源目录为空，请先在功能资源页创建目录项。</p>}
            </>}
          </section>
        )}

        {tab === "finance" && (
          <section className="control-entitlement-section" id="company-control-panel-finance" role="tabpanel" aria-labelledby="company-control-tab-finance">
            <header><div><h3>企业用量与账务</h3><p>{billingUnitLabel(summary)}；积分消费不等于人民币收入，真实收款由支付与财务账确认。</p></div><button type="button" onClick={onRecharge} disabled={!canRecharge || !legacyManualRechargeAvailable} title={legacyManualRechargeAvailable ? "历史人民币账人工入账" : "积分入账需可信支付或授权来源，本入口暂不可用"}><Plus size={15} /> {legacyManualRechargeAvailable ? "历史人工入账" : "积分入账暂不可用"}</button></header>
            {drawer.loadingDomains?.finance ? (
              <CollectionState state="loading" title="正在读取用量与账务" detail="权益页可独立使用，不会等待此请求。" />
            ) : drawer.domainErrors?.finance ? (
              <CollectionState state="error" title="用量与账务读取失败" detail={drawer.domainErrors.finance} onRetry={onReload} />
            ) : <>
            <dl className="control-company-finance-strip">
              <div><dt>累计入账</dt><dd>{drawer.recharges ? billingTotalsLabel(drawer.recharges, { pointsField: "total_amount_points", centsField: "total_amount_cents" }) : billingAmountLabel(summary, { pointsField: "recharge_points", centsField: "recharge_cents" })}</dd></div>
              <div><dt>可用权益</dt><dd>{billingAmountLabel(summary, { pointsField: "available_points", centsField: "available_cents" })}</dd></div>
              <div><dt>已预留</dt><dd>{billingAmountLabel(summary, { pointsField: "reserved_points", centsField: "reserved_cents" })}</dd></div>
              <div><dt>实际消费</dt><dd>{drawer.consumption ? billingTotalsLabel(drawer.consumption, { pointsField: "total_amount_points", centsField: "total_amount_cents" }) : billingAmountLabel(summary, { pointsField: "consumption_points", centsField: "consumption_cents" })}</dd></div>
              <div><dt>任务数量</dt><dd>{summary.task_count ?? "未提供"}</dd></div>
            </dl>

            <div className="control-company-ledger-section">
              <header><strong>权益入账记录</strong><span>{drawer.recharges?.total || 0} 笔</span></header>
              <div className="control-table-wrap"><table className="control-table"><thead><tr><th>入账权益</th><th>说明</th><th>时间</th></tr></thead><tbody>{(drawer.recharges?.items || []).map((item) => <tr key={item.id}><td className="is-positive">{billingAmountLabel(item, { pointsField: item.amount_points == null ? "available_delta_points" : "amount_points", centsField: item.amount_cents == null ? "available_delta_cents" : "amount_cents", signed: true })}</td><td>{item.note || "平台账本入账"}</td><td>{shortDate(item.created_at)}</td></tr>)}{!(drawer.recharges?.items || []).length && <EmptyRows colSpan={3} message="暂无权益入账记录" />}</tbody></table></div>
              <PageLoadMore loaded={drawer.recharges?.items?.length} total={drawer.recharges?.total} busy={paginationBusyKey === "company-control-recharges"} onLoadMore={() => onLoadMore("recharges")} noun="笔入账" />
            </div>

            <div className="control-company-ledger-section">
              <header><strong>消费记录</strong><span>{drawer.consumption?.total || 0} 笔</span></header>
              <div className="control-table-wrap"><table className="control-table"><thead><tr><th>员工</th><th>模型</th><th>数量</th><th>实际消费</th><th>时间</th></tr></thead><tbody>{(drawer.consumption?.items || []).map((item) => <tr key={item.ledger_entry_id}><td><strong>{item.employee_display_name}</strong><small>{item.employee_email}</small></td><td>{item.model_display_name}</td><td>{pricingQuantityLabel(item)}</td><td>{billingAmountLabel(item, { pointsField: "amount_points", centsField: "amount_cents" })}</td><td>{shortDate(item.consumed_at)}</td></tr>)}{!(drawer.consumption?.items || []).length && <EmptyRows colSpan={5} message="暂无成功结算消费" />}</tbody></table></div>
              <PageLoadMore loaded={drawer.consumption?.items?.length} total={drawer.consumption?.total} busy={paginationBusyKey === "company-control-consumption"} onLoadMore={() => onLoadMore("consumption")} noun="笔消费" />
            </div>
            </>}
          </section>
        )}
      </div>

      <footer className="control-company-panel-footer"><QuietButton onClick={onClose} disabled={Boolean(busyKey)}>关闭</QuietButton><QuietButton onClick={onReload} disabled={Boolean(busyKey) || Object.values(drawer.loadingDomains || {}).some(Boolean)}><ArrowClockwise size={16} /> 刷新可访问数据</QuietButton></footer>
    </div>
  );
}
