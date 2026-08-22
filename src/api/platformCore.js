const PRODUCTION_BUILD = import.meta.env?.PROD === true;

let platformCsrfToken = "";

export function setPlatformCsrfToken(value) {
  platformCsrfToken = String(value || "").trim();
}

export function getPlatformCsrfToken() {
  return platformCsrfToken;
}

export function clearPlatformCsrfToken() {
  platformCsrfToken = "";
}

function makeRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `req-${Date.now()}-${Math.random()}`;
}

function withQuery(path, values = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const serialized = query.toString();
  return serialized ? `${path}?${serialized}` : path;
}

export class PlatformApiError extends Error {
  constructor(message, { status = 0, code = "NETWORK_ERROR", requestId, details } = {}) {
    super(message);
    this.name = "PlatformApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export function readRuntimePlatformConfig(runtime = globalThis, buildConfig = {}) {
  const supplied = runtime.__AI_VIDEO_RUNTIME_CONFIG__;
  const config = supplied && typeof supplied === "object" ? supplied : {};
  const stored = (key) => {
    try {
      return runtime.sessionStorage?.getItem(key) ?? "";
    } catch {
      return "";
    }
  };

  return {
    baseUrl: String(
      config.platformApiUrl ||
        buildConfig.platformApiUrl ||
        runtime.location?.origin ||
        "",
    ).trim(),
    companyId: String(
      config.companyId ||
        stored("ai-video.company-id") ||
        "",
    ).trim(),
    accessToken: !PRODUCTION_BUILD && config.legacyBearerEnabled === true
      ? String(stored("ai-video.access-token") || "").trim()
      : "",
  };
}

function normalizeApiBaseUrl(value) {
  if (!value) return "";
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new PlatformApiError("客户平台 API 地址无效", {
      code: "INVALID_PLATFORM_API_URL",
    });
  }
  const isLoopback = ["localhost", "127.0.0.1", "[::1]"].includes(
    url.hostname,
  );
  if (url.username || url.password) {
    throw new PlatformApiError("客户平台 API 地址不得包含凭据", {
      code: "UNSAFE_PLATFORM_API_URL",
    });
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new PlatformApiError("客户平台 API 地址不得包含查询参数或片段", {
      code: "UNSAFE_PLATFORM_API_URL",
    });
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && isLoopback)) {
    throw new PlatformApiError("客户平台 API 必须使用 HTTPS", {
      code: "INSECURE_PLATFORM_API_URL",
    });
  }
  return url.origin;
}

export function parseArtifactDownloadUrl(
  value,
  { allowLocalHttp = false } = {},
) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new PlatformApiError("平台返回的下载地址无效", {
      code: "INVALID_DOWNLOAD_URL",
    });
  }
  if (url.username || url.password) {
    throw new PlatformApiError("平台返回的下载地址不得包含凭据", {
      code: "UNSAFE_DOWNLOAD_URL",
    });
  }

  const isLoopback = ["localhost", "127.0.0.1", "[::1]"].includes(
    url.hostname,
  );
  if (
    url.protocol !== "https:" &&
    !(allowLocalHttp && isLoopback && url.protocol === "http:")
  ) {
    throw new PlatformApiError("平台返回的下载地址必须使用 HTTPS", {
      code: "INSECURE_DOWNLOAD_URL",
    });
  }
  return url;
}

export function createPlatformCore({
  baseUrl,
  companyId,
  accessToken,
  csrfToken,
  fetcher = globalThis.fetch,
  requestTimeoutMs = 15_000,
} = {}) {
  const runtimeConfig = readRuntimePlatformConfig();
  const normalizedBaseUrl = normalizeApiBaseUrl(
    String(baseUrl ?? runtimeConfig.baseUrl).trim(),
  );
  const normalizedCompanyId = String(
    companyId ?? runtimeConfig.companyId,
  ).trim();
  const suppliedAccessToken = String(
    accessToken ?? runtimeConfig.accessToken,
  ).trim();
  const normalizedAccessToken = PRODUCTION_BUILD ? "" : suppliedAccessToken;
  if (typeof fetcher !== "function") {
    throw new TypeError("A fetch implementation is required");
  }
  if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0) {
    throw new TypeError("requestTimeoutMs must be a positive number");
  }
  if (/\s|[\u0000-\u001f\u007f]/u.test(normalizedAccessToken)) {
    throw new PlatformApiError("登录令牌格式无效", {
      code: "INVALID_ACCESS_TOKEN",
    });
  }

  function companyPath(suffix = "") {
    if (!normalizedCompanyId) {
      throw new PlatformApiError("尚未配置公司 ID", {
        code: "COMPANY_ID_NOT_CONFIGURED",
      });
    }
    return `/api/v1/companies/${encodeURIComponent(normalizedCompanyId)}${suffix}`;
  }

  function resolvePlatformUrl(value) {
    const raw = String(value || "").trim();
    if (!raw || !normalizedBaseUrl) {
      throw new PlatformApiError("平台媒体地址无效", {
        code: "INVALID_PLATFORM_MEDIA_URL",
      });
    }
    let url;
    try {
      url = new URL(raw, normalizedBaseUrl);
    } catch {
      throw new PlatformApiError("平台媒体地址无效", {
        code: "INVALID_PLATFORM_MEDIA_URL",
      });
    }
    if (
      url.origin !== normalizedBaseUrl
      || url.username
      || url.password
      || !["http:", "https:"].includes(url.protocol)
    ) {
      throw new PlatformApiError("平台媒体地址必须属于已配置的 Platform", {
        code: "UNSAFE_PLATFORM_MEDIA_URL",
      });
    }
    return url.href;
  }

  async function request(
    path,
    {
      method = "GET",
      body,
      idempotencyKey,
      etag,
      signal,
      responseType = "json",
      timeoutMs = requestTimeoutMs,
      companyContext = true,
      acceptNotModified = false,
      includeResponseMetadata = false,
    } = {},
  ) {
    if (!normalizedBaseUrl) {
      throw new PlatformApiError("尚未配置客户平台 API", {
        code: "PLATFORM_API_NOT_CONFIGURED",
      });
    }
    const requestId = makeRequestId();
    const isFormData =
      typeof FormData !== "undefined" && body instanceof FormData;
    const headers = {
      Accept: responseType === "text" ? "text/csv, text/plain" : "application/json",
      "X-Request-Id": requestId,
    };
    if (normalizedAccessToken) headers.Authorization = `Bearer ${normalizedAccessToken}`;

    if (companyContext && normalizedCompanyId) {
      headers["X-Company-ID"] = normalizedCompanyId;
    }
    if (body !== undefined && !isFormData) {
      headers["Content-Type"] = "application/json";
    }
    if (idempotencyKey) {
      headers["Idempotency-Key"] = idempotencyKey;
    }
    if (etag) {
      const normalizedEtag = String(etag).trim();
      if (/\r|\n/u.test(normalizedEtag)) {
        throw new PlatformApiError("缓存版本标识格式无效", {
          code: "INVALID_ETAG",
          requestId,
        });
      }
      headers["If-None-Match"] = normalizedEtag;
    }
    if (!normalizedAccessToken && !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
      const activeCsrfToken = String(
        typeof csrfToken === "function"
          ? csrfToken()
          : csrfToken ?? getPlatformCsrfToken(),
      ).trim();
      if (activeCsrfToken) headers["X-CSRF-Token"] = activeCsrfToken;
    }

    const requestController = new AbortController();
    let timedOut = false;
    const abortFromCaller = () => requestController.abort(signal?.reason);
    if (signal?.aborted) {
      abortFromCaller();
    } else {
      signal?.addEventListener("abort", abortFromCaller, { once: true });
    }
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new TypeError("timeoutMs must be a positive number");
    }
    const timeout = setTimeout(() => {
      timedOut = true;
      requestController.abort();
    }, timeoutMs);

    let response;
    try {
      response = await fetcher(`${normalizedBaseUrl}${path}`, {
        method,
        headers,
        credentials: normalizedAccessToken ? "omit" : "include",
        redirect: "error",
        body:
          body === undefined
            ? undefined
            : isFormData
              ? body
              : JSON.stringify(body),
        signal: requestController.signal,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      if (timedOut) {
        throw new PlatformApiError("客户平台请求超时，请稍后重试", {
          code: "REQUEST_TIMEOUT",
          requestId,
        });
      }
      throw new PlatformApiError("无法连接客户平台 API", {
        requestId,
        details: { cause: error?.message ?? String(error) },
      });
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abortFromCaller);
    }

    const responseRequestId = response.headers?.get?.("x-request-id") ?? requestId;
    const responseEtag = String(response.headers?.get?.("etag") || "").trim();
    const contentType = response.headers?.get?.("content-type") ?? "";
    const isJson = contentType.toLowerCase().includes("application/json");
    if (response.status === 304 && acceptNotModified) {
      return {
        data: null,
        etag: responseEtag || String(etag || ""),
        notModified: true,
        status: 304,
      };
    }
    if (response.ok && response.status === 204) {
      return includeResponseMetadata
        ? { data: null, etag: responseEtag, notModified: false, status: 204 }
        : null;
    }
    let payload;
    try {
      payload = isJson ? await response.json() : await response.text();
    } catch (error) {
      throw new PlatformApiError("客户平台返回了无法解析的响应", {
        code: "INVALID_RESPONSE",
        requestId: responseRequestId,
        details: {
          httpStatus: response.status,
          contentType,
          cause: error?.message ?? String(error),
        },
      });
    }

    if (response.ok && responseType === "json" && !isJson) {
      throw new PlatformApiError("客户平台返回了非 JSON 响应", {
        code: "INVALID_RESPONSE",
        requestId: responseRequestId,
        details: { httpStatus: response.status, contentType },
      });
    }

    if (!response.ok) {
      const nestedError = payload?.error;
      const detail = nestedError ?? payload?.detail ?? payload;
      const message =
        typeof detail === "string"
          ? detail
          : detail?.message ?? `请求失败（HTTP ${response.status}）`;
      const authRequired = String(
        response.headers?.get?.("x-auth-required") || "",
      ).trim().toLowerCase();
      const responseDetails = nestedError?.details
        ?? payload?.details
        ?? detail?.details;
      throw new PlatformApiError(message, {
        status: response.status,
        code: authRequired === "step-up"
          ? "STEP_UP_REQUIRED"
          : nestedError?.code ?? payload?.code ?? detail?.code ?? "HTTP_ERROR",
        requestId: responseRequestId,
        details: authRequired === "step-up"
          ? {
            ...(responseDetails && typeof responseDetails === "object"
              && !Array.isArray(responseDetails) ? responseDetails : {}),
            authRequired: "step-up",
          }
          : responseDetails,
      });
    }

    return includeResponseMetadata
      ? {
          data: payload,
          etag: responseEtag,
          notModified: false,
          status: response.status,
        }
      : payload;
  }

  return {
    isConfigured: Boolean(normalizedBaseUrl && normalizedCompanyId),
    isSessionConfigured: Boolean(normalizedBaseUrl),
    companyId: normalizedCompanyId,
    request,
    companyPath,
    resolvePlatformUrl,
    makeRequestId,
    withQuery,
    PlatformApiError,
  };
}
