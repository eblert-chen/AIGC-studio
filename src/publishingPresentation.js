const PUBLISHING_BLOCKER_LABELS = Object.freeze({
  feature_not_configured: "平台尚未配置自动发布",
  feature_retired: "自动发布功能已停用",
  company_grant_missing: "公司尚未获得自动发布权限",
  company_grant_disabled: "公司的自动发布权限已停用",
  company_grant_not_yet_effective: "公司的自动发布权限尚未生效",
  company_grant_expired: "公司的自动发布权限已过期",
  no_publish_manage_permission: "当前账号只能查看，不能执行新的发布操作",
});

const PUBLISHING_PERMISSION_LABELS = Object.freeze({
  "publish.accounts.read": "查看发布账号",
  "publish.accounts.manage": "管理发布账号",
  "publish.jobs.read": "查看发布任务",
  "publish.jobs.manage": "管理发布任务",
});

const AUTHENTICATION_METHOD_LABELS = Object.freeze({
  pwd: "密码",
  password: "密码",
  otp: "一次性验证码",
  sms: "短信验证码",
  email: "邮箱验证码",
  mfa: "多因素验证",
  webauthn: "通行密钥",
  passkey: "通行密钥",
  fido2: "安全密钥",
  hwk: "硬件安全密钥",
});

const OAUTH_FAILURE_MESSAGES = Object.freeze({
  invalid_or_expired_state: "授权请求已失效，请重新发起连接。",
  provider_denied: "你已取消授权，发布账号尚未连接。",
  missing_code: "发布平台没有返回完整授权结果，请重新发起连接。",
  authorization_revoked: "连接期间账号权限发生变化，请确认权限后重试。",
  exchange_failed: "服务端未能完成授权交换，请稍后重试。",
});

const PUBLISHER_LABELS = Object.freeze({
  douyin: "抖音",
  tiktok: "TikTok",
  mock: "开发测试平台",
});

const CONNECTION_STATUS_LABELS = Object.freeze({
  active: "可用",
  disabled: "已停用",
  requires_reauth: "需要重新授权",
});

const KNOWN_BACKEND_MESSAGES = Object.freeze({
  "Publisher connection is not active": "发布账号当前不可用，请重新授权或更换账号。",
  "Company is not entitled to feature.auto_publish": "公司当前未获得自动发布权限。",
});

function normalizeCode(value) {
  return String(value || "").trim().toLowerCase();
}

function blockerCode(value) {
  if (typeof value === "string") return normalizeCode(value);
  return normalizeCode(value?.code || value?.reason || value?.key);
}

function looksLikeMachineCode(value) {
  return /^(?:[A-Z][A-Z0-9_]*|[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+)$/.test(value);
}

export function publishingBlockerLabel(value) {
  return PUBLISHING_BLOCKER_LABELS[blockerCode(value)]
    || "发布条件尚未满足，请联系公司管理员核对授权与权限";
}

export function publishingBlockerSummary(values) {
  const labels = [...new Set(
    (Array.isArray(values) ? values : [])
      .map(publishingBlockerLabel)
      .filter(Boolean),
  )];
  return labels.join("；");
}

export function publishingPermissionLabel(value) {
  return PUBLISHING_PERMISSION_LABELS[String(value || "").trim()]
    || "所需的发布权限";
}

export function publishingPermissionGuidance(value) {
  return `请联系公司管理员为你开启“${publishingPermissionLabel(value)}”权限。`;
}

export function authenticationMethodLabel(value) {
  return AUTHENTICATION_METHOD_LABELS[normalizeCode(value)] || "身份服务验证";
}

export function oauthFailureMessage(reason) {
  return OAUTH_FAILURE_MESSAGES[normalizeCode(reason)]
    || "发布账号连接未完成，请重新发起授权。";
}

export function publishingProviderLabel(value, providers = []) {
  const normalized = normalizeCode(value);
  const provider = (Array.isArray(providers) ? providers : []).find(
    (item) => normalizeCode(item?.provider) === normalized,
  );
  const displayName = String(provider?.display_name || "").trim();
  if (displayName && !looksLikeMachineCode(displayName)) return displayName;
  return PUBLISHER_LABELS[normalized] || "其他发布平台";
}

export function publishingConnectionStatusLabel(value) {
  return CONNECTION_STATUS_LABELS[normalizeCode(value)] || "状态待确认";
}

export function safePublishingMessage(value, fallback = "发布服务未完成请求，请稍后重试。") {
  const message = String(value || "").trim();
  if (!message) return fallback;
  if (KNOWN_BACKEND_MESSAGES[message]) return KNOWN_BACKEND_MESSAGES[message];
  if (PUBLISHING_BLOCKER_LABELS[normalizeCode(message)]) {
    return publishingBlockerLabel(message);
  }
  if (PUBLISHING_PERMISSION_LABELS[message]) {
    return publishingPermissionGuidance(message);
  }
  if (looksLikeMachineCode(message)) return fallback;
  return message;
}

export function findVerifiedOAuthConnection(connections, provider) {
  const normalizedProvider = normalizeCode(provider);
  if (!normalizedProvider) return null;
  return (Array.isArray(connections) ? connections : []).find((connection) => (
    normalizeCode(connection?.provider) === normalizedProvider
    && normalizeCode(connection?.status) === "active"
  )) || null;
}
