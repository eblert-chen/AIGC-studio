const SECURITY_HEADERS = {
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

function configuredHttpsOrigin(env, name) {
  const value = env?.[name];
  if (typeof value !== "string" || !value.trim()) return "";

  try {
    const url = new URL(value.trim());
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    ) {
      return "";
    }
    return url.origin;
  } catch {
    return "";
  }
}

function configuredPlatformOrigin(env) {
  return configuredHttpsOrigin(env, "PLATFORM_API_ORIGIN");
}

function configuredShowcaseMediaOrigin(env) {
  return configuredHttpsOrigin(env, "PLATFORM_SHOWCASE_MEDIA_ORIGIN");
}

function contentSecurityPolicy(request, env) {
  const requestOrigin = new URL(request.url).origin;
  const platformOrigin = configuredPlatformOrigin(env);
  const showcaseMediaOrigin = configuredShowcaseMediaOrigin(env);
  const connectSources = ["'self'"];
  if (platformOrigin && platformOrigin !== requestOrigin) {
    connectSources.push(platformOrigin);
  }
  const imageSources = ["'self'", "data:", "blob:"];
  const mediaSources = ["'self'", "blob:"];
  for (const origin of [platformOrigin, showcaseMediaOrigin]) {
    if (!origin || origin === requestOrigin) continue;
    if (!imageSources.includes(origin)) imageSources.push(origin);
    if (!mediaSources.includes(origin)) mediaSources.push(origin);
  }

  return [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    `img-src ${imageSources.join(" ")}`,
    `media-src ${mediaSources.join(" ")}`,
    `connect-src ${connectSources.join(" ")}`,
    "upgrade-insecure-requests",
  ].join("; ");
}

function secure(response, request, env) {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    headers.set(name, value);
  }
  headers.set("Content-Security-Policy", contentSecurityPolicy(request, env));
  if (new URL(request.url).protocol === "https:") {
    headers.set(
      "Strict-Transport-Security",
      "max-age=31536000; includeSubDomains",
    );
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export default {
  async fetch(request, env) {
    const response = await env.ASSETS.fetch(request);
    const acceptsHtml = request.headers.get("accept")?.includes("text/html");
    const pathname = new URL(request.url).pathname;
    const isReservedPath =
      pathname === "/api" ||
      pathname.startsWith("/api/") ||
      pathname === "/health" ||
      pathname.startsWith("/health/");

    if (
      response.status !== 404 ||
      isReservedPath ||
      !acceptsHtml ||
      !["GET", "HEAD"].includes(request.method)
    ) {
      return secure(response, request, env);
    }

    const indexUrl = new URL(request.url);
    indexUrl.pathname = "/index.html";
    indexUrl.search = "";
    return secure(
      await env.ASSETS.fetch(new Request(indexUrl, request)),
      request,
      env,
    );
  },
};
