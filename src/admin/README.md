# Operations Console integration

```jsx
import { AdminOperationsConsole } from "./admin/index.js";

<AdminOperationsConsole
  data={operationsData}
  loading={loading}
  error={errorMessage}
  administrator={{ name: admin.display_name, roleLabel: admin.role_label }}
  onRangeChange={reloadRange}
  onRefresh={reloadCurrentSection}
  onExceptionAction={resolveException}
  onEntitlementSave={saveGrant}
  onBatchEntitlementCommit={commitGrantBatch}
  onAuditExport={exportAudit}
  onAuditRollback={createInverseChange}
  onAdminAccessSave={savePlatformAdminPermissions}
/>
```

`demoMode` defaults to `false`. Missing live data produces honest empty states; sample values are used only when `demoMode={true}`.

## Data groups

The `data` object accepts these independent groups, so the host can load only the active module:

- `summary`, `taskFlow`, `timings`, `trends`, `failureTrends`, `failureReasons`, `latencyDistribution`, `exceptions`, `reliability`
- `business: { metrics, trend, companyRanking }`
- `modelProfitability`, `companyHealth`, `channels`
- `publishingExceptions`, `assetExceptions`
- `relayChannels`, `relayUnknownSubmissions`, `relayCallbackDeadLetters`
- `companies`, `entitlementProducts`, `entitlementTemplates`, `entitlementGrants`
- `auditEvents`, `platformAdmins`, `adminPermissionCatalog`

Entitlement grants use a map keyed by `companyId::productId`. Each grant may contain `state`, `priceCents`, `quota`, `concurrency`, `effectiveAt`, `expiresAt`, and `capabilityLimit`.

## Mutation callbacks

- `onExceptionAction({ action, exception, note })`
- `onEntitlementSave({ companyId, productId, grant, reason })`
- `onBatchEntitlementCommit(previewWithReason)`
- `onAuditExport({ range, items })`
- `onAuditRollback({ event, reason })`
- `onAdminAccessSave({ adminId, permissions, reason })`
- `onRelayChannelDetail(channel)`
- `onRelayChannelOperation({ channel, kind, values })`
- `onRelayChannelOperationRead({ channel, kind, values })`
- navigation/detail callbacks: `onSectionChange`, `onCompanyOpen`, `onModelOpen`, `onReliabilityAction`

Callbacks may return promises. The console keeps drawers open and shows an error if a mutation rejects. In live mode, a missing mutation callback never produces a fake success.
