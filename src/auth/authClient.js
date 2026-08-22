import {
  PlatformApiError,
  clearPlatformCsrfToken,
  getPlatformCsrfToken,
  setPlatformCsrfToken,
} from "../api/platformClient.js";

const SAFE_PROMPTS = new Set(["login", "step_up", "select_account"]);
const AUTH_SESSION_UI_KEYS = new Set([
  "ai-video.access-token",
  "ai-video.company-id",
  "ai-video.surface",
]);
const SESSION_UI_PREFIXES = ["ai-video.pending-create:"];

function makeRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `auth-${Date.now()}-${Math.random()}`;
}

function normalizeBaseUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new PlatformApiError("客户平台地址无效", {
      code: "INVALID_PLATFORM_API_URL",
    });
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash ||
    (url.protocol !== "https:" && !(loopback && url.protocol === "http:"))
  ) {
    throw new PlatformApiError("客户平台地址不符合安全要求", {
      code: "UNSAFE_PLATFORM_API_URL",
    });
  }
  return url.origin;
}

function responseError(payload, response, requestId) {
  const nested = payload?.error;
  const detail = nested ?? payload?.detail ?? payload;
  const message = typeof detail === "string"
    ? detail
    : detail?.message ?? `请求失败（HTTP ${response.status}）`;
  const authRequired = String(response.headers?.get?.("x-auth-required") || "")
    .trim()
    .toLowerCase();
  return new PlatformApiError(message, {
    status: response.status,
    code: authRequired === "step-up"
      ? "STEP_UP_REQUIRED"
      : nested?.code ?? payload?.code ?? detail?.code ?? "HTTP_ERROR",
    requestId: response.headers?.get?.("x-request-id") || requestId,
    details: nested?.details ?? payload?.details ?? detail?.details,
  });
}

function rememberCsrf(response, payload) {
  const token = String(
    response.headers?.get?.("x-csrf-token") || payload?.csrf_token || "",
  ).trim();
  if (token) setPlatformCsrfToken(token);
}

export function safeReturnTo(
  value,
  runtime = globalThis,
  fallback = "/",
) {
  const origin = String(runtime.location?.origin || "").trim();
  if (!origin) return fallback;
  const raw = String(value || "").trim();
  if (!raw || raw.length > 2048) return fallback;
  try {
    const url = new URL(raw, origin);
    if (
      url.origin !== origin ||
      url.username ||
      url.password ||
      !["http:", "https:"].includes(url.protocol)
    ) return fallback;
    const candidate = `${url.pathname}${url.search}${url.hash}`;
    if (
      url.pathname === "/login" ||
      url.pathname === "/auth/callback" ||
      url.pathname === "/auth/logout"
    ) return fallback;
    return candidate || fallback;
  } catch {
    return fallback;
  }
}

export function currentReturnTo(runtime = globalThis) {
  const location = runtime.location;
  if (!location) return "/";
  return safeReturnTo(
    `${location.pathname || "/"}${location.search || ""}${location.hash || ""}`,
    runtime,
  );
}

function loginReturnTo(value, runtime) {
  const candidate = safeReturnTo(value, runtime);
  try {
    const url = new URL(candidate, runtime.location?.origin);
    // Invitation capabilities stay in the backend HttpOnly handoff cookie and never cross OIDC URLs.
    if (url.pathname === "/invite") return "/invite";
  } catch {
    return "/";
  }
  return candidate;
}

function pageQuery(path, { page = 1, pageSize = 20 } = {}) {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return `${path}?${query.toString()}`;
}

function removeLegacyInvitationStorage(runtime = globalThis) {
  try {
    runtime.sessionStorage?.removeItem("ai-video.pending-invitation-token");
    runtime.localStorage?.removeItem("ai-video.pending-invitation-token");
  } catch {
    // Legacy browser state is ignored; the HttpOnly handoff cookie is authoritative.
  }
}

export function captureInvitationToken(runtime = globalThis) {
  removeLegacyInvitationStorage(runtime);
  let token = "";
  try {
    const hash = String(runtime.location?.hash || "").replace(/^#/, "");
    token = String(new URLSearchParams(hash).get("token") || "").trim();
  } catch {
    token = "";
  }
  return token.length <= 512 ? token : "";
}

export function establishInvitationHandoff(runtime = globalThis) {
  removeLegacyInvitationStorage(runtime);
  if (runtime.location?.hash) {
    const cleanUrl = `${runtime.location.pathname || "/invite"}${runtime.location.search || ""}`;
    runtime.history?.replaceState?.({}, "", cleanUrl);
  }
}

export function clearInvitationToken(runtime = globalThis) {
  establishInvitationHandoff(runtime);
}

export function clearAuthSessionState(runtime = globalThis) {
  clearPlatformCsrfToken();
  removeLegacyInvitationStorage(runtime);
  const storage = runtime.sessionStorage;
  if (!storage) return;
  try {
    for (const key of AUTH_SESSION_UI_KEYS) storage.removeItem(key);
  } catch {
    // The HttpOnly server session remains authoritative when storage is unavailable.
  }
}

export function clearSessionUiState(runtime = globalThis) {
  clearAuthSessionState(runtime);
  removeLegacyInvitationStorage(runtime);
  const storage = runtime.sessionStorage;
  if (!storage) return;
  try {
    const keys = [];
    for (let index = 0; index < Number(storage.length || 0); index += 1) {
      const key = storage.key?.(index);
      if (key) keys.push(key);
    }
    for (const key of keys) {
      if (
        AUTH_SESSION_UI_KEYS.has(key) ||
        SESSION_UI_PREFIXES.some((prefix) => key.startsWith(prefix))
      ) storage.removeItem(key);
    }
  } catch {
    // Server-side session invalidation remains authoritative when storage is unavailable.
  }
}

export function normalizeAuthSession(payload) {
  const source = payload?.session && typeof payload.session === "object"
    ? { ...payload, ...payload.session }
    : payload;
  const authenticated = source?.authenticated === true && Boolean(source?.user);
  return {
    ...(source && typeof source === "object" ? source : {}),
    authenticated,
    csrf_token: authenticated ? String(source?.csrf_token || "") : "",
    user: authenticated ? {
      ...source.user,
      id: String(source.user?.id || source.user?.user_id || ""),
      email: String(source.user?.email || ""),
      display_name: String(source.user?.display_name || source.user?.name || ""),
      status: String(source.user?.status || "active"),
      email_verified_at: source.user?.email_verified_at || null,
    } : null,
    account_management_url: String(source?.account_management_url || ""),
    session_expires_at: source?.session_expires_at || null,
  };
}

export function normalizeInvitation(payload) {
  const source = payload?.invitation && typeof payload.invitation === "object"
    ? payload.invitation
    : payload;
  return {
    ...(source && typeof source === "object" ? source : {}),
    id: String(source?.id || source?.invitation_id || ""),
    company_id: String(source?.company_id || ""),
    company_name: String(source?.company_name || source?.company?.name || ""),
    email: String(source?.email || ""),
    display_name: String(source?.display_name || ""),
    inviter_name: String(source?.inviter_name || source?.invited_by_name || ""),
    primary_role: String(source?.primary_role || source?.role || "operator"),
    status: String(source?.status || "pending").toLowerCase(),
    expires_at: source?.expires_at || null,
  };
}

export function safeAccountManagementUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (
      url.username ||
      url.password ||
      (url.protocol !== "https:" && !(loopback && url.protocol === "http:"))
    ) return "";
    return url.href;
  } catch {
    return "";
  }
}

export function createAuthClient({
  baseUrl,
  fetcher = globalThis.fetch,
  runtime = globalThis,
  requestTimeoutMs = 15_000,
} = {}) {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl || runtime.location?.origin || "");
  if (typeof fetcher !== "function") throw new TypeError("A fetch implementation is required");

  async function request(path, {
    method = "GET",
    body,
    signal,
    timeoutMs = requestTimeoutMs,
  } = {}) {
    const requestId = makeRequestId();
    const controller = new AbortController();
    let timedOut = false;
    const abortFromCaller = () => controller.abort(signal?.reason);
    if (signal?.aborted) abortFromCaller();
    else signal?.addEventListener("abort", abortFromCaller, { once: true });
    const timeout = globalThis.setTimeout?.(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const headers = {
      Accept: "application/json",
      "X-Request-Id": requestId,
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
      const csrf = getPlatformCsrfToken();
      if (csrf) headers["X-CSRF-Token"] = csrf;
    }

    let response;
    try {
      response = await fetcher(`${normalizedBaseUrl}${path}`, {
        method,
        headers,
        credentials: "include",
        redirect: "error",
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new PlatformApiError(
        timedOut ? "账号服务响应超时，请稍后重试" : "无法连接账号服务",
        {
          code: timedOut ? "REQUEST_TIMEOUT" : "NETWORK_ERROR",
          requestId,
          details: { cause: error?.message ?? String(error) },
        },
      );
    } finally {
      if (timeout !== undefined) globalThis.clearTimeout?.(timeout);
      signal?.removeEventListener?.("abort", abortFromCaller);
    }

    const contentType = String(response.headers?.get?.("content-type") || "").toLowerCase();
    let payload = null;
    if (response.status !== 204) {
      try {
        payload = contentType.includes("application/json")
          ? await response.json()
          : await response.text();
      } catch (error) {
        throw new PlatformApiError("账号服务返回了无法解析的响应", {
          code: "INVALID_RESPONSE",
          status: response.status,
          requestId: response.headers?.get?.("x-request-id") || requestId,
          details: { cause: error?.message ?? String(error) },
        });
      }
    }
    rememberCsrf(response, payload);
    if (!response.ok) throw responseError(payload, response, requestId);
    return payload;
  }

  const invitationToken = (token) => {
    const normalized = String(token || "").trim();
    if (!normalized || normalized.length > 512) {
      throw new PlatformApiError("邀请链接无效", { code: "INVALID_INVITATION_TOKEN" });
    }
    return normalized;
  };

  return {
    baseUrl: normalizedBaseUrl,
    getSession: async ({ signal } = {}) => normalizeAuthSession(
      await request("/api/v1/auth/session", { signal }),
    ),
    loginUrl: ({ returnTo = currentReturnTo(runtime), prompt = "login" } = {}) => {
      const url = new URL("/api/v1/auth/login", `${normalizedBaseUrl}/`);
      url.searchParams.set("return_to", loginReturnTo(returnTo, runtime));
      url.searchParams.set("prompt", SAFE_PROMPTS.has(prompt) ? prompt : "login");
      return url.href;
    },
    logout: ({ preserveInvitation = false, signal } = {}) => request("/api/v1/auth/logout", {
      method: "POST",
      body: { preserve_invitation: preserveInvitation === true },
      signal,
    }),
    getAccount: ({ signal } = {}) => request("/api/v1/account", { signal }),
    updateAccount: ({ displayName, expectedAuthVersion, expectedUpdatedAt }, { signal } = {}) => request("/api/v1/account", {
      method: "PATCH",
      body: {
        display_name: String(displayName || "").trim(),
        expected_auth_version: expectedAuthVersion,
        expected_updated_at: expectedUpdatedAt,
      },
      signal,
    }),
    listSessions: ({ page = 1, pageSize = 20, signal } = {}) => request(
      pageQuery("/api/v1/account/sessions", { page, pageSize }),
      { signal },
    ),
    revokeSession: (sessionId, { signal } = {}) => request(
      `/api/v1/account/sessions/${encodeURIComponent(String(sessionId || ""))}`,
      { method: "DELETE", signal },
    ),
    revokeAllSessions: ({ signal } = {}) => request(
      "/api/v1/account/sessions/revoke-all",
      { method: "POST", body: {}, signal },
    ),
    deactivateAccount: ({ expectedAuthVersion }, { signal } = {}) => request("/api/v1/account/deactivate", {
      method: "POST",
      body: {
        expected_auth_version: expectedAuthVersion,
        confirmation: "DEACTIVATE",
      },
      signal,
    }),
    getInvitation: async (token = "", { signal } = {}) => normalizeInvitation(
      await request("/api/v1/invitations/preview", {
        method: "POST",
        body: String(token || "").trim()
          ? { token: invitationToken(token) }
          : {},
        signal,
      }),
    ),
    acceptInvitation: async ({ signal } = {}) => request(
      "/api/v1/invitations/accept",
      { method: "POST", body: {}, signal },
    ),
  };
}
