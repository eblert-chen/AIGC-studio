import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { PlatformApiError, readRuntimePlatformConfig } from "../api/platformClient.js";
import { AuthCallbackPage } from "./AuthCallbackPage.jsx";
import { AuthShell } from "./AuthShell.jsx";
import { InvitationPage } from "./InvitationPage.jsx";
import { LoginPage } from "./LoginPage.jsx";
import {
  clearAuthSessionState,
  clearInvitationToken,
  clearSessionUiState,
  captureInvitationToken,
  createAuthClient,
  currentReturnTo,
  establishInvitationHandoff,
  isAuthUnavailableError,
  safeReturnTo,
} from "./authClient.js";
import { createAuthSessionSync } from "./sessionSync.js";

const LOGIN_NAVIGATION_TIMEOUT_MS = 8_000;

const AuthContext = createContext({
  status: "loading",
  session: null,
  client: null,
  loginAvailable: false,
  beginLogin: () => {},
  refreshSession: async () => null,
  logout: async () => {},
  switchAccount: async () => {},
  switchProductContext: async () => null,
  switchInvitationAccount: async () => {},
  finishSession: () => {},
  handleAuthenticationError: () => false,
});

function authRoute(pathname) {
  const path = String(pathname || "/").replace(/\/+$/, "") || "/";
  if (path === "/login") return { kind: "login" };
  if (path === "/auth/callback") return { kind: "callback" };
  if (path === "/invite") return { kind: "invitation" };
  return { kind: "app" };
}

function AuthenticatedLoginRedirect({ returnTo }) {
  useEffect(() => {
    globalThis.location?.replace?.(returnTo);
  }, [returnTo]);
  return (
    <AuthShell variant="login" eyebrow="账号已登录" title="正在继续" description="当前安全会话仍然有效。" tone="success" busy />
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthGateway({ children, demoMode = false }) {
  const runtimeConfig = readRuntimePlatformConfig(globalThis, {
    platformApiUrl: import.meta.env.VITE_PLATFORM_API_URL,
  });
  const clientResult = useMemo(() => {
    try {
      return { client: createAuthClient({ baseUrl: runtimeConfig.baseUrl }), error: null };
    } catch (error) {
      return { client: null, error };
    }
  }, [runtimeConfig.baseUrl]);
  const client = clientResult.client;
  const [status, setStatus] = useState(demoMode ? "demo" : "loading");
  const [session, setSession] = useState(null);
  const [loginAvailable, setLoginAvailable] = useState(false);
  const [error, setError] = useState(clientResult.error?.message || "");
  const [logoutIntent, setLogoutIntent] = useState(null);
  const [route, setRoute] = useState(() => authRoute(globalThis.location?.pathname));
  const [invitationToken, setInvitationToken] = useState(() => (
    authRoute(globalThis.location?.pathname).kind === "invitation"
      ? captureInvitationToken()
      : ""
  ));
  const requestRef = useRef(0);
  const sessionRef = useRef(null);
  const syncRef = useRef(null);
  const loginNavigationRef = useRef(false);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  const publishSessionEvent = useCallback((type, details) => (
    syncRef.current?.publish(type, details)
  ), []);

  const refreshSession = useCallback(async ({ silent = false, broadcast = true } = {}) => {
    if (demoMode || !client) return null;
    const request = ++requestRef.current;
    if (!silent) {
      loginNavigationRef.current = false;
      setStatus("loading");
    }
    setError("");
    try {
      const nextSession = await client.getSession();
      if (request !== requestRef.current) return nextSession;
      const previousSession = sessionRef.current;
      if (nextSession.authenticated) {
        setLoginAvailable(nextSession.login_available === true);
        sessionRef.current = nextSession;
        setSession(nextSession);
        setStatus("authenticated");
        const previousVersion = previousSession?.user?.auth_version;
        const nextVersion = nextSession.user?.auth_version;
        if (
          broadcast
          && Number.isInteger(previousVersion)
          && Number.isInteger(nextVersion)
          && previousVersion !== nextVersion
        ) {
          publishSessionEvent("account_version_changed", {
            authVersion: nextVersion,
            fullClear: false,
          });
        }
      } else {
        const wasAuthenticated = Boolean(previousSession?.authenticated);
        const nextLoginAvailable = nextSession.login_available === true;
        clearAuthSessionState();
        sessionRef.current = null;
        setSession(null);
        setLoginAvailable(nextLoginAvailable);
        if (nextLoginAvailable) {
          setStatus("anonymous");
        } else {
          setError("账号登录尚未配置，请联系管理员。");
          setStatus("error");
        }
        if (broadcast && wasAuthenticated) {
          publishSessionEvent("invalidated", { fullClear: false });
        }
      }
      return nextSession;
    } catch (nextError) {
      if (request !== requestRef.current) return null;
      const authUnavailable = isAuthUnavailableError(nextError);
      const explicitlyUnauthenticated = nextError instanceof PlatformApiError && (
        nextError.status === 401 || nextError.code === "UNAUTHENTICATED"
      );
      if (explicitlyUnauthenticated) {
        clearAuthSessionState();
        sessionRef.current = null;
        setSession(null);
        setLoginAvailable(true);
        setStatus("anonymous");
        if (broadcast) publishSessionEvent("invalidated", { fullClear: false });
        return null;
      }
      if (authUnavailable) {
        clearAuthSessionState();
        sessionRef.current = null;
        setSession(null);
        setLoginAvailable(false);
        setError("账号登录尚未配置，请联系管理员。");
        setStatus("error");
        return null;
      }
      setLoginAvailable(false);
      setError(nextError?.message || "无法确认登录状态");
      setStatus((current) => current === "authenticated" ? current : "error");
      return null;
    }
  }, [client, demoMode, publishSessionEvent]);

  useEffect(() => {
    if (!demoMode && client) refreshSession();
  }, [client, demoMode, refreshSession]);

  useEffect(() => {
    if (demoMode || !client) return undefined;
    const sync = createAuthSessionSync({
      onEvent: (event) => {
        requestRef.current += 1;
        if (event.full_clear) {
          clearSessionUiState(globalThis, {
            preserveInvitation: event.preserve_invitation,
          });
        } else {
          clearAuthSessionState();
        }
        sessionRef.current = null;
        setSession(null);
        setError("");
        setStatus("loading");
        refreshSession({ broadcast: false });
      },
    });
    syncRef.current = sync;
    return () => {
      if (syncRef.current === sync) syncRef.current = null;
      sync.close();
    };
  }, [client, demoMode, refreshSession]);

  useEffect(() => {
    const onPopState = () => {
      const nextRoute = authRoute(globalThis.location?.pathname);
      setRoute(nextRoute);
      if (nextRoute.kind === "invitation") setInvitationToken(captureInvitationToken());
    };
    globalThis.addEventListener?.("popstate", onPopState);
    globalThis.addEventListener?.("hashchange", onPopState);
    return () => {
      globalThis.removeEventListener?.("popstate", onPopState);
      globalThis.removeEventListener?.("hashchange", onPopState);
    };
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return undefined;
    const refreshWhenVisible = () => {
      if (globalThis.document?.visibilityState === "visible") refreshSession({ silent: true });
    };
    const refreshAfterBackForwardCache = (event) => {
      if (event?.persisted === true) refreshSession({ silent: true });
    };
    globalThis.document?.addEventListener?.("visibilitychange", refreshWhenVisible);
    globalThis.addEventListener?.("focus", refreshWhenVisible);
    globalThis.addEventListener?.("pageshow", refreshAfterBackForwardCache);
    return () => {
      globalThis.document?.removeEventListener?.("visibilitychange", refreshWhenVisible);
      globalThis.removeEventListener?.("focus", refreshWhenVisible);
      globalThis.removeEventListener?.("pageshow", refreshAfterBackForwardCache);
    };
  }, [refreshSession, status]);

  useEffect(() => {
    if (status !== "authenticated" || !session?.session_expires_at) return undefined;
    const expiry = new Date(session.session_expires_at).getTime();
    if (!Number.isFinite(expiry)) return undefined;
    const delay = Math.min(Math.max(1_000, expiry - Date.now() - 30_000), 2_147_483_647);
    const timer = globalThis.setTimeout?.(() => refreshSession({ silent: true }), delay);
    return () => globalThis.clearTimeout?.(timer);
  }, [refreshSession, session?.session_expires_at, status]);

  useEffect(() => {
    if (status !== "navigating") return undefined;
    const recoverNavigation = () => {
      if (!loginNavigationRef.current) return;
      loginNavigationRef.current = false;
      setLoginAvailable(false);
      setError("登录页面未能打开，请重新检测账号服务后再试。");
      setStatus("error");
    };
    const recoverAfterBackForwardCache = (event) => {
      if (event?.persisted === true) recoverNavigation();
    };
    const timer = globalThis.setTimeout?.(recoverNavigation, LOGIN_NAVIGATION_TIMEOUT_MS);
    globalThis.addEventListener?.("pageshow", recoverAfterBackForwardCache);
    return () => {
      globalThis.clearTimeout?.(timer);
      globalThis.removeEventListener?.("pageshow", recoverAfterBackForwardCache);
    };
  }, [status]);

  const beginLogin = useCallback(({ returnTo, prompt = "login" } = {}) => {
    if (!client || loginNavigationRef.current) return;
    if (!loginAvailable) {
      setError("账号登录当前不可用，请重新检测服务状态。");
      setStatus("error");
      return;
    }
    try {
      const target = client.loginUrl({
        returnTo: safeReturnTo(returnTo || currentReturnTo()),
        prompt,
      });
      if (typeof globalThis.location?.assign !== "function") {
        throw new Error("当前浏览器无法打开登录页面");
      }
      loginNavigationRef.current = true;
      setError("");
      setStatus("navigating");
      globalThis.location.assign(target);
    } catch (nextError) {
      loginNavigationRef.current = false;
      setLoginAvailable(false);
      setError(nextError?.message || "登录页面未能打开，请重新检测账号服务后再试。");
      setStatus("error");
    }
  }, [client, loginAvailable]);

  const finishSession = useCallback(({
    to = "/login",
    fullClear = true,
    preserveInvitation = false,
    broadcastType = "",
  } = {}) => {
    requestRef.current += 1;
    if (fullClear) clearSessionUiState(globalThis, { preserveInvitation });
    else clearAuthSessionState();
    sessionRef.current = null;
    loginNavigationRef.current = false;
    setSession(null);
    setError("");
    setLogoutIntent(null);
    setStatus("anonymous");
    const destination = new URL(to, globalThis.location?.origin || "http://localhost");
    if (`${globalThis.location?.pathname || ""}${globalThis.location?.search || ""}` !== `${destination.pathname}${destination.search}`) {
      globalThis.history?.replaceState?.({}, "", to);
      setRoute(authRoute(destination.pathname));
    }
    if (broadcastType) {
      publishSessionEvent(broadcastType, {
        preserveInvitation,
        fullClear,
      });
    }
  }, [publishSessionEvent]);

  const completeLogout = useCallback((intent) => {
    const switchingInvitationAccount = intent?.kind === "switch_invitation_account";
    const switchingAccount = switchingInvitationAccount || intent?.kind === "switch_account";
    const switchReturnTo = switchingInvitationAccount
      ? "/invite"
      : safeReturnTo(intent?.returnTo || "/");
    finishSession({
      to: switchingInvitationAccount
        ? "/invite"
        : switchingAccount
          ? `/login?return_to=${encodeURIComponent(switchReturnTo)}`
          : "/login?logged_out=1",
      preserveInvitation: switchingInvitationAccount,
      broadcastType: "logout",
    });
    if (switchingAccount) {
      beginLogin({ returnTo: switchReturnTo, prompt: "select_account" });
    }
  }, [beginLogin, finishSession]);

  const requestLogout = useCallback(async (intent) => {
    setLogoutIntent(intent);
    setError("");
    setStatus("logging_out");
    try {
      await client?.logout({
        preserveInvitation: intent?.kind === "switch_invitation_account",
      });
      completeLogout(intent);
      return { error: null, confirmed: true };
    } catch (nextError) {
      if (nextError instanceof PlatformApiError && nextError.status === 401) {
        completeLogout(intent);
        return { error: nextError, confirmed: true };
      }
      requestRef.current += 1;
      clearSessionUiState();
      sessionRef.current = null;
      setSession(null);
      setError(nextError?.message || "无法确认服务端是否已撤销当前会话。");
      setStatus("logout_uncertain");
      return { error: nextError, confirmed: false };
    }
  }, [client, completeLogout]);

  const logout = useCallback(async () => requestLogout({ kind: "logout" }), [requestLogout]);

  const switchAccount = useCallback(async ({ returnTo = "/" } = {}) => requestLogout({
    kind: "switch_account",
    returnTo: safeReturnTo(returnTo),
  }), [requestLogout]);

  const switchProductContext = useCallback(async ({ targetContext } = {}) => {
    if (demoMode) return null;
    if (!client) {
      throw new PlatformApiError("账号服务当前不可用", {
        code: "AUTH_CLIENT_UNAVAILABLE",
      });
    }
    const nextSession = await client.switchProductContext({ targetContext });
    if (!nextSession?.authenticated) {
      throw new PlatformApiError("产品空间切换未返回有效会话", {
        code: "INVALID_PRODUCT_CONTEXT_SESSION",
      });
    }
    requestRef.current += 1;
    sessionRef.current = nextSession;
    setSession(nextSession);
    setLoginAvailable(nextSession.login_available === true);
    setError("");
    setStatus("authenticated");
    publishSessionEvent("product_context_changed", { fullClear: false });
    return nextSession;
  }, [client, demoMode, publishSessionEvent]);

  const switchInvitationAccount = useCallback(async () => requestLogout({
    kind: "switch_invitation_account",
  }), [requestLogout]);

  const handleAuthenticationError = useCallback((nextError) => {
    if (!(nextError instanceof PlatformApiError)) return false;
    if (nextError.code === "STEP_UP_REQUIRED") {
      beginLogin({ returnTo: currentReturnTo(), prompt: "step_up" });
      return true;
    }
    if (isAuthUnavailableError(nextError)) {
      requestRef.current += 1;
      clearAuthSessionState();
      sessionRef.current = null;
      loginNavigationRef.current = false;
      setSession(null);
      setLoginAvailable(false);
      setError("账号登录尚未配置，请联系管理员。");
      setStatus("error");
      return true;
    }
    if (nextError.status === 401) {
      finishSession({
        to: "/login",
        fullClear: false,
        broadcastType: "invalidated",
      });
      return true;
    }
    return false;
  }, [beginLogin, finishSession]);

  const value = useMemo(() => ({
    status,
    session,
    client,
    loginAvailable,
    beginLogin,
    refreshSession,
    logout,
    switchAccount,
    switchProductContext,
    switchInvitationAccount,
    finishSession,
    handleAuthenticationError,
  }), [
    beginLogin,
    client,
    finishSession,
    handleAuthenticationError,
    loginAvailable,
    logout,
    refreshSession,
    session,
    status,
    switchAccount,
    switchProductContext,
    switchInvitationAccount,
  ]);

  if (demoMode) return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;

  if (!client) {
    return (
      <AuthContext.Provider value={value}>
        <LoginPage
          error="账号服务地址无效，请联系管理员。"
          onRetry={() => globalThis.location?.reload?.()}
        />
      </AuthContext.Provider>
    );
  }

  if (status === "logging_out" || status === "logout_uncertain") {
    const switchingAccount = ["switch_account", "switch_invitation_account"]
      .includes(logoutIntent?.kind);
    return (
      <AuthContext.Provider value={value}>
        <AuthShell
          eyebrow={status === "logging_out" ? "正在撤销会话" : "退出未确认"}
          title={status === "logging_out" ? "正在安全退出当前账号" : "服务端尚未确认退出"}
          description={status === "logging_out"
            ? "收到服务端确认前不会宣称退出，也不会开始切换账号。"
            : `${error || "账号服务暂时不可达。"} 当前 HttpOnly 会话仍可能有效，页面已隐藏业务数据且不会继续${switchingAccount ? "换号" : "退出跳转"}。`}
          tone={status === "logging_out" ? "loading" : "warning"}
          busy={status === "logging_out"}
        >
          {status === "logout_uncertain" ? (
            <button className="auth-primary-action" type="button" autoFocus onClick={() => requestLogout(logoutIntent || { kind: "logout" })}>
              重试并确认服务端退出
            </button>
          ) : null}
        </AuthShell>
      </AuthContext.Provider>
    );
  }

  if (route.kind === "invitation") {
    return (
      <AuthContext.Provider value={value}>
        <InvitationPage
          token={invitationToken}
          client={client}
          session={session}
          authStatus={status}
          onPreviewEstablished={() => {
            establishInvitationHandoff();
            setInvitationToken("");
          }}
          onLogin={beginLogin}
          onSwitchAccount={switchInvitationAccount}
          onAbandon={() => {
            clearInvitationToken();
            globalThis.location?.assign?.(session?.authenticated ? "/" : "/login");
          }}
          onAuthenticationError={handleAuthenticationError}
          onAccepted={(invitation) => {
            clearInvitationToken();
            const companyId = invitation?.company_id;
            if (companyId) {
              try { globalThis.sessionStorage?.setItem("ai-video.company-id", companyId); } catch { /* use server default */ }
            }
            globalThis.location?.assign?.("/");
          }}
        />
      </AuthContext.Provider>
    );
  }

  if (route.kind === "callback") {
    return (
      <AuthContext.Provider value={value}>
        <AuthCallbackPage session={session} status={status} onRetry={() => refreshSession()} />
      </AuthContext.Provider>
    );
  }

  const query = new URLSearchParams(globalThis.location?.search || "");
  const requestedReturnTo = safeReturnTo(query.get("return_to") || "/");

  if (status === "loading") {
    return (
      <AuthContext.Provider value={value}>
        <AuthShell variant="login" eyebrow="会话安全检查" title="正在确认账号" description="正在连接安全身份服务。" tone="loading" busy />
      </AuthContext.Provider>
    );
  }

  if (status === "authenticated") {
    if (route.kind === "login") {
      return (
        <AuthContext.Provider value={value}>
          <AuthenticatedLoginRedirect returnTo={requestedReturnTo} />
        </AuthContext.Provider>
      );
    }
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
  }

  return (
    <AuthContext.Provider value={value}>
      <LoginPage
        error={status === "error" ? error : ""}
        loggedOut={query.get("logged_out") === "1"}
        deactivated={query.get("deactivated") === "1"}
        busy={status === "navigating"}
        returnTo={route.kind === "login" ? requestedReturnTo : currentReturnTo()}
        onLogin={beginLogin}
        onRetry={() => refreshSession()}
      />
    </AuthContext.Provider>
  );
}
