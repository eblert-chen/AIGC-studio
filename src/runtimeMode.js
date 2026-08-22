function enabledFlag(value) {
  return String(value ?? "").trim().toLowerCase() === "true";
}

/**
 * Demo data is a development-only, explicit opt-in.  Production wins over
 * every other hint so a misconfigured VITE_ENABLE_DEMO can never open a
 * synthetic workspace in a release build.
 */
export function isExplicitDevelopmentDemo(env = {}) {
  const mode = String(env.MODE ?? "").trim().toLowerCase();
  const production = env.PROD === true || mode === "production";
  const development = env.DEV === true || mode === "development";
  return !production && development && enabledFlag(env.VITE_ENABLE_DEMO);
}

export function readBuildPlatformConfig(env = {}) {
  return {
    platformApiUrl: String(env.VITE_PLATFORM_API_URL ?? "").trim(),
  };
}

function safeAuthNavigationUrl(value, runtime) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw, runtime.location?.origin);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (url.username || url.password) return "";
    if (url.protocol !== "https:" && !(loopback && url.protocol === "http:")) return "";
    return url.href;
  } catch {
    return "";
  }
}

export function readRuntimeAuthNavigation(runtime = globalThis) {
  const supplied = runtime.__AI_VIDEO_RUNTIME_CONFIG__;
  const config = supplied && typeof supplied === "object" ? supplied : {};
  return {
    loginUrl: safeAuthNavigationUrl(config.loginUrl, runtime),
    logoutUrl: safeAuthNavigationUrl(config.logoutUrl, runtime),
  };
}
