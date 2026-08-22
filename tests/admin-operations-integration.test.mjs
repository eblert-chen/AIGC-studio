import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { operationsSource as operations } from "./operations-source.mjs";

const management = readFileSync(new URL("../src/ManagementConsole.jsx", import.meta.url), "utf8");
const container = readFileSync(new URL("../src/admin/AdminOperationsContainer.jsx", import.meta.url), "utf8");
const adapter = readFileSync(new URL("../src/admin/adminApiAdapter.js", import.meta.url), "utf8");
const nativeConsole = readFileSync(new URL("../src/admin/relayNativeConsole.js", import.meta.url), "utf8");
const operationsStyles = readFileSync(new URL("../src/design-system/operations-routes.css", import.meta.url), "utf8");
const operationTokens = readFileSync(new URL("../src/design-system/tokens.css", import.meta.url), "utf8");

test("platform operations console is lazy-loaded while company management stays on the legacy console", () => {
  assert.match(management, /React\.lazy\(\s*\(\) => import\("\.\/admin\/AdminOperationsContainer\.jsx"\)/);
  assert.match(management, /if \(props\.mode === "platform"\) return <PlatformManagementRouter/);
  assert.match(management, /return <LegacyManagementConsole \{\.\.\.props\} \/>/);
});

test("platform administrators can switch between operations and permission-filtered base configuration", () => {
  assert.match(management, /onOpenBasicConfig/);
  assert.match(management, /onOpenOperationsConsole/);
  assert.match(management, /PLATFORM_SECTION_PERMISSIONS/);
  assert.match(operations, /className="ops-basic-config-label">基础配置<\/span>/);
  assert.match(management, />运营指挥台/);
});

test("the complete operations workspace survives a basic-configuration round trip", () => {
  assert.match(management, /const \[operationsContext, setOperationsContext\] = useState\(\{/);
  assert.match(management, /activeSection: "task-operations"/);
  assert.match(management, /range: "24h"/);
  assert.match(management, /operationsContext=\{operationsContext\}/);
  assert.match(management, /onOperationsContextChange=\{setOperationsContext\}/);
  assert.match(management, /basicConfigButton\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(management, /className="platform-operations-surface"/);
  assert.match(management, /hidden=\{view !== "operations"\}/);
  assert.match(management, /inert=\{view !== "operations"\}/);
  assert.match(management, /\{operationsSurface\}[\s\S]*view === "legacy"/);
  assert.doesNotMatch(management, /if \(view === "legacy"\) \{[\s\S]*return \([\s\S]*<LegacyManagementConsole/);
  assert.match(container, /operationsContext = \{\}/);
  assert.match(container, /onOperationsContextChange\?\.\(\{ activeSection: nextSection, range \}\)/);
  assert.match(container, /onOperationsContextChange\?\.\(\{ activeSection, range: nextRange \}\)/);
  assert.match(container, /range=\{range\}/);
});

test("live loading is permission-aware and keeps unrequested modules empty", () => {
  assert.match(container, /getAdminDataReadiness/);
  assert.match(container, /hasPermissions\(me, "platform\.analytics\.read"/);
  assert.match(container, /hasPermissions\(me, "platform\.entitlements\.read"/);
  assert.match(container, /hasPermissions\(me, "platform\.admin_access\.read"/);
  assert.match(container, /adaptAdminOperationsData\(result\.raw\)/);
  assert.doesNotMatch(container, /DEMO_ADMIN_OPERATIONS_DATA/);
});

test("the administrator overview renders the server-owned production data readiness verdict", () => {
  assert.match(operations, /production_data_ready=/);
  assert.match(operations, /blockingSources/);
  assert.match(operations, /Relay 签名遥测/);
  assert.match(operations, /<DataReadinessStatus readiness=\{data\.dataReadiness\}/);
});

test("entitlement mutations always use preview snapshots before execute", () => {
  assert.match(container, /previewAdminEntitlementBatch/);
  assert.match(container, /executeAdminEntitlementBatch\(\{[\s\S]*expected_snapshot: preview\.snapshot/);
  assert.match(container, /previewAdminEntitlementCopy/);
  assert.match(container, /executeAdminEntitlementCopy/);
  assert.match(container, /previewAdminEntitlementTemplate/);
  assert.match(container, /executeAdminEntitlementTemplate/);
});

test("submission-unknown exceptions are never auto-reconciled by the console", () => {
  assert.match(container, /DOWNLOAD_REGISTRATION_UNKNOWN/);
  assert.match(container, /reconcileAdminDownloadGatewayAttempt/);
  assert.doesNotMatch(container, /reconcilePublicationJob/);
  assert.doesNotMatch(container, /reconcileAdminPublication/);
});

test("Relay unknown-submission UI is wired through read, fresh detail, approval, and refresh fencing", () => {
  assert.match(container, /hasPermissions\(me, "platform\.relay_health\.read"\)[\s\S]*listAdminRelayUnknownSubmissions/);
  assert.match(container, /getAdminRelayUnknownSubmission\(item\.jobId\)/);
  assert.match(container, /hasPermissions\(identity, "platform\.relay_health\.manage"\)/);
  assert.match(container, /buildRelayUnknownResolution\(item, form\)/);
  assert.match(container, /resolveAdminRelayUnknownSubmission\(jobId, resolution\)/);
  assert.match(container, /getAdminRelayUnknownSubmissionResult\(jobId, \{ operationId \}\)/);
  assert.match(container, /onRelayUnknownRefresh=\{demoMode \? undefined : refreshRelayUnknown\}/);
  assert.match(operations, /明确审批确认/);
  assert.match(operations, /route、attempt 与 token fencing/);
  assert.match(operations, /relayUnknownPendingForm/);
  assert.match(operations, /页面未再次提交 resolve/);
  assert.match(operations, /只会读取 pending 详情或 Relay receipt/);
  assert.match(operations, /演示处置已完成（未调用 Relay）/);
  assert.match(operations, /setRelayUnknownRequiresRefresh\(true\)/);
  assert.match(operations, /requiresRefresh \|\| !ready/);
  assert.doesNotMatch(operations, /setInterval\([^)]*resolveRelayUnknown/);
});

test("Relay callback dead letters use Platform list/detail/redrive/result with one POST and readback", () => {
  assert.match(container, /listAdminRelayCallbackDeadLetters/);
  assert.match(container, /getAdminRelayCallbackDeadLetter/);
  assert.match(container, /redriveAdminRelayCallbackDeadLetter/);
  assert.match(container, /getAdminRelayCallbackRedriveResult/);
  assert.match(container, /redriveRelayCallbackWithReadback/);
  assert.match(operations, /Callback 死信队列/);
  assert.match(operations, /网络结果不明时，页面只读取 redrive 回执，绝不重复 POST/);
  assert.match(operations, /setRelayCallbackDeadLetterRequiresReadback\(true\)/);
  assert.match(operations, /busy \|\| requiresReadback \|\| !ready/);
  assert.match(operations, /本窗口不会再次 POST，也不会生成新的 operation_id/);
  assert.doesNotMatch(operations, /setInterval\([^)]*redriveRelayCallback/);
});

test("Relay channel control stays behind Platform with stable operation fencing and safe rendering", () => {
  assert.match(container, /loadCompleteRelayChannels/);
  assert.match(container, /listAdminRelayChannels/);
  assert.match(container, /getAdminRelayChannel/);
  assert.match(container, /testAdminRelayChannel/);
  assert.match(container, /setAdminRelayChannelStatus/);
  assert.match(container, /getAdminRelayChannelOperation/);
  assert.match(container, /runRelayChannelOperationWithReadback/);
  assert.match(container, /platform\.relay_health\.manage/);
  assert.doesNotMatch(container, /configuredRelayAdminUrl|globalThis\.open|newApiAdminUrl/);
  assert.match(operations, /Relay 渠道控制面/);
  assert.match(operations, /凭据状态/);
  assert.match(operations, /停用只阻止新准入/);
  assert.match(operations, /需 staging 真实 canary/);
  assert.match(operations, /本次 operation_id 已锁定/);
  assert.match(operations, /页面只会读取同一个 operation_id，不会再次 POST/);
  assert.match(operations, /演示操作已完成（未调用 Relay）/);
  assert.doesNotMatch(operations, /进入 new-api|需在中转站查看/);
  assert.doesNotMatch(operations, /credential\.key_count|base_url|proxy|fingerprint/);
  assert.doesNotMatch(operations, /setInterval\([^)]*submitRelayChannelOperation/);
});

test("Relay high-risk operations use an owner-only two-step Platform authorization", () => {
  assert.match(container, /openAdminRelayNativeConsole/);
  assert.match(container, /requestRelayNativeConsoleGrant/);
  assert.match(container, /identity\?\.is_platform_owner === true/);
  assert.match(container, /canAuthorizeRelayNativeConsole/);
  assert.match(operations, /高风险运维/);
  assert.match(operations, /独立登录/);
  assert.match(operations, /新标签页/);
  assert.match(operations, /target="_blank"/);
  assert.match(operations, /rel="noopener noreferrer"/);
  assert.match(operations, /referrerPolicy="no-referrer"/);
  assert.match(operations, /deferRelayNativeConsoleGrantConsumption/);
  assert.match(operations, /60_000/);
  assert.match(operations, /尚未证明 new-api 已打开或登录/);
  assert.doesNotMatch(operations, /<iframe/i);
  assert.doesNotMatch(nativeConsole, /localStorage|sessionStorage|history\.|window\.open|globalThis\.open/);
});

test("operations dialogs trap keyboard focus and restore the invoking control", () => {
  assert.match(operations, /const previousFocus = document\.activeElement instanceof HTMLElement/);
  assert.match(operations, /event\.key !== "Tab"/);
  assert.match(operations, /document\.activeElement === last/);
  assert.match(operations, /if \(previousFocus\?\.isConnected\) previousFocus\.focus\(\)/);
  assert.match(operations, /aria-modal="true"[\s\S]*tabIndex="-1"/);
});

test("model-profit rows expose a named keyboard-operable details control", () => {
  assert.match(operations, /className="ops-model-row-link"/);
  assert.match(operations, /aria-label=\{`查看 \$\{row\.model\} 模型利润详情`\}/);
  assert.match(operations, /event\.stopPropagation\(\); onModelOpen\(row\)/);
});

test("small operational copy uses the accessible muted token", () => {
  assert.match(operationTokens, /--ops-muted:\s*var\(--text-muted\)/);
  assert.match(operationTokens, /--ops-accent:/);
  assert.match(operationTokens, /--ops-blue:\s*var\(--info\)/);
  assert.match(operationTokens, /--ops-red:\s*var\(--danger\)/);
  assert.match(operationTokens, /--ops-orange:\s*var\(--warning\)/);
  assert.match(operationsStyles, /\.ops-last-refresh \{[\s\S]*color: var\(--ops-muted\)/);
  assert.match(operationsStyles, /\.ops-empty \{[\s\S]*color: var\(--ops-muted\)/);
  assert.match(operationsStyles, /\.ops-reason-row small \{[\s\S]*color: var\(--ops-muted\)/);
  assert.match(operationsStyles, /\.ops-form-field textarea::placeholder,[\s\S]*color: var\(--ops-muted\)/);
  assert.match(operationsStyles, /\.ops-entitlement-cell\.is-disabled \{[\s\S]*color: var\(--ops-muted\)/);
  assert.doesNotMatch(operations, /fill: "#70807b"/);
  assert.doesNotMatch(operationsStyles, /#[0-9a-f]{3,8}\b/i);
  assert.doesNotMatch(operationsStyles, /rgba?\(/i);
  assert.doesNotMatch(operations, /#(?:ef6d65|67b9bf|e8c45e|76a7d6|29aeb2|e79638)/);
  assert.doesNotMatch(operations, /#c7dcd7/);
  assert.match(operations, /cyan: "var\(--ops-chart-cyan\)"/);
  assert.match(operations, /contentReview: "var\(--ops-chart-warning\)"/);
  assert.match(operationsStyles, /--ops-chart-cyan:\s*color-mix\([^;]+var\(--ops-accent\)[^;]+var\(--ops-blue\)\)/);
  assert.match(operationsStyles, /--ops-chart-warning:\s*var\(--ops-orange\)/);
});

test("operations location state is recoverable and titles follow the active module", () => {
  assert.match(container, /ops_module/);
  assert.match(container, /ops_range/);
  assert.match(container, /globalThis\.history\[push \? "pushState" : "replaceState"\]/);
  assert.match(container, /addEventListener\?\.\("popstate", restoreFromHistory\)/);
  assert.match(operations, /ops_audit_tab/);
  assert.match(operations, /ops_audit_query/);
  assert.match(operations, /ops_audit_result/);
  assert.match(operations, /globalThis\.document\.title = nextTitle/);
});

test("charts expose keyboard-readable values and unknown counts never stringify null", () => {
  assert.match(operations, /<details className="ops-chart-data">/);
  assert.match(operations, /<summary>查看图表数值<\/summary>/);
  assert.match(operations, /caption="任务流转趋势数值"/);
  assert.match(operations, /caption="平台经营趋势数值"/);
  assert.match(operations, /evidenceCount\(data\.relayUnknownSubmissionTotal, "待核验"\)/);
  assert.match(operations, /evidenceCount\(data\.relayCallbackDeadLetterTotal, "待核验"\)/);
});

test("partial datasets stay explicit and the initial permission verdict never flashes", () => {
  assert.match(container, /const sourceStatuses = Object\.fromEntries/);
  assert.match(container, /sourceStatuses\[request\.key\] = "available"/);
  assert.match(container, /result\.reason\?\.status === 401[\s\S]*?"unauthorized"[\s\S]*?: "failed"/);
  assert.match(container, /accessPending=\{!demoMode && loading && !identity\}/);
  assert.match(operations, /accessPending[\s\S]*?正在核验管理员身份与模块授权/);
  assert.match(operations, /本区域不是零数据，请重试或检查服务状态/);
});

test("Operations keeps read-only evidence inspectable without exposing mutation controls", () => {
  assert.match(operations, /function EntitlementDrawer\(\{[\s\S]*?readOnly/);
  assert.match(operations, /if \(readOnly\) return/);
  assert.match(operations, /readOnly \? <button className="ops-secondary-button"/);
  assert.match(operations, /<EntitlementDrawer \{\.\.\.entitlementCell\}/);
  assert.match(operations, /return JSON\.stringify\(value\)/);
  assert.match(adapter, /item\.result \|\| item\.outcome \|\| item\.status \|\| "recorded"/);
  assert.match(operations, /showRange=\{TIME_SCOPED_SECTIONS\.has\(renderSection\)\}/);
  assert.match(operations, /const chartRows = data\.modelProfitability\.map\(\(\{ id: _id, \.\.\.row \}\) => row\)/);
});
