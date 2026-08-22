const NATIVE_CONSOLE_MODE = "native_break_glass";
const NATIVE_CONSOLE_PATH = "/channels";
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export class RelayNativeConsoleGrantError extends Error {
  constructor(message, code) {
    super(message);
    this.name = "RelayNativeConsoleGrantError";
    this.code = code;
  }
}

export function relayNativeConsoleAuthorizationAllowed({
  demoMode = false,
  isPlatformOwner = false,
  canManageRelayHealth = false,
} = {}) {
  return !demoMode && isPlatformOwner === true && canManageRelayHealth === true;
}

export function relayNativeConsoleAuthorizationDisabledReason({
  demoMode = false,
  isPlatformOwner = false,
  canManageRelayHealth = false,
} = {}) {
  if (demoMode) return "演示模式不开放真实 Relay 原生控制台。";
  if (!isPlatformOwner) return "仅平台所有者可授权 Relay 高风险运维入口。";
  if (!canManageRelayHealth) return "当前平台所有者缺少 Relay 管理权限。";
  return "";
}

export function validateRelayNativeConsoleGrant(
  payload,
  { allowLocalHttp = false } = {},
) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new RelayNativeConsoleGrantError(
      "Platform 返回的 Relay 原生控制台授权结构无效。",
      "INVALID_NATIVE_CONSOLE_GRANT",
    );
  }

  const keys = Object.keys(payload).sort();
  if (keys.length !== 2 || keys[0] !== "mode" || keys[1] !== "url") {
    throw new RelayNativeConsoleGrantError(
      "Platform 返回了未约定的 Relay 原生控制台授权字段。",
      "INVALID_NATIVE_CONSOLE_GRANT",
    );
  }
  if (payload.mode !== NATIVE_CONSOLE_MODE || typeof payload.url !== "string") {
    throw new RelayNativeConsoleGrantError(
      "Platform 返回的 Relay 原生控制台授权模式无效。",
      "INVALID_NATIVE_CONSOLE_GRANT",
    );
  }
  if (
    payload.url !== payload.url.trim()
    || /[\u0000-\u001f\u007f]/u.test(payload.url)
    || payload.url.includes("?")
    || payload.url.includes("#")
  ) {
    throw new RelayNativeConsoleGrantError(
      "Relay 原生控制台地址不得携带空白、控制字符、查询参数或片段。",
      "UNSAFE_NATIVE_CONSOLE_URL",
    );
  }

  let url;
  try {
    url = new URL(payload.url);
  } catch {
    throw new RelayNativeConsoleGrantError(
      "Platform 返回的 Relay 原生控制台地址无效。",
      "INVALID_NATIVE_CONSOLE_URL",
    );
  }

  if (url.username || url.password || url.search || url.hash) {
    throw new RelayNativeConsoleGrantError(
      "Relay 原生控制台地址不得携带凭据、查询参数或片段。",
      "UNSAFE_NATIVE_CONSOLE_URL",
    );
  }
  const localHttpAllowed = allowLocalHttp
    && url.protocol === "http:"
    && LOOPBACK_HOSTS.has(url.hostname);
  if (url.protocol !== "https:" && !localHttpAllowed) {
    throw new RelayNativeConsoleGrantError(
      "Relay 原生控制台必须使用 HTTPS；仅本地开发允许 loopback HTTP。",
      "INSECURE_NATIVE_CONSOLE_URL",
    );
  }
  if (url.pathname !== NATIVE_CONSOLE_PATH) {
    throw new RelayNativeConsoleGrantError(
      "Relay 原生控制台授权只能打开固定的渠道管理路径。",
      "INVALID_NATIVE_CONSOLE_PATH",
    );
  }

  return Object.freeze({
    mode: NATIVE_CONSOLE_MODE,
    url: url.href,
  });
}

export async function requestRelayNativeConsoleGrant({
  requestAccess,
  allowLocalHttp = false,
}) {
  if (typeof requestAccess !== "function") {
    throw new TypeError("requestAccess must be a function");
  }
  const payload = await requestAccess();
  return validateRelayNativeConsoleGrant(payload, { allowLocalHttp });
}

export function deferRelayNativeConsoleGrantConsumption(
  consume,
  schedule = globalThis.setTimeout,
) {
  if (typeof consume !== "function") return;
  if (typeof schedule !== "function") {
    throw new TypeError("schedule must be a function");
  }
  schedule(consume, 0);
}

export function relayNativeConsoleBlockReason(error) {
  if (error?.code === "STEP_UP_REQUIRED") return "";
  if (error?.status === 403) return "forbidden";
  if (
    error?.status === 503
    || error?.code === "RELAY_NATIVE_CONSOLE_NOT_CONFIGURED"
    || error?.code === "NATIVE_CONSOLE_NOT_CONFIGURED"
  ) {
    return "unconfigured";
  }
  return "";
}

export function relayNativeConsoleErrorMessage(error) {
  if (error?.code === "STEP_UP_REQUIRED") {
    return "需重新完成平台管理员强认证后，再授权打开 Relay 高风险运维入口。";
  }
  if (error?.status === 403) {
    return "当前平台管理员没有打开 Relay 高风险运维入口的权限。";
  }
  if (
    error?.status === 503
    || error?.code === "RELAY_NATIVE_CONSOLE_NOT_CONFIGURED"
    || error?.code === "NATIVE_CONSOLE_NOT_CONFIGURED"
  ) {
    return "Platform 尚未配置 Relay 原生控制台入口；配置完成后请刷新页面。";
  }
  return error?.message || "Relay 原生控制台入口授权失败，请稍后重试。";
}
