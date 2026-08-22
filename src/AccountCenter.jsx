import { useEffect, useMemo, useState } from "react";
import {
  ArrowSquareOut,
  Check,
  ClockCounterClockwise,
  DeviceMobile,
  Laptop,
  LockKey,
  SignOut,
  SpinnerGap,
  Trash,
  UserCircle,
  WarningCircle,
} from "@phosphor-icons/react";
import { useAuth } from "./auth/AuthGateway.jsx";
import { currentReturnTo, safeAccountManagementUrl } from "./auth/authClient.js";

function formatDateTime(value) {
  if (!value) return "未提供";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function normalizeAccount(payload, session) {
  const source = payload?.account || payload?.user || payload || session?.user || {};
  return {
    ...source,
    id: String(source.id || source.user_id || session?.user?.id || ""),
    email: String(source.email || session?.user?.email || ""),
    display_name: String(source.display_name || source.name || session?.user?.display_name || ""),
    status: String(source.status || session?.user?.status || "active"),
    email_verified_at: source.email_verified_at || session?.user?.email_verified_at || null,
  };
}

function normalizeSessions(payload) {
  const items = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items)
      ? payload.items
      : Array.isArray(payload?.sessions) ? payload.sessions : [];
  return items.map((item) => ({
    ...item,
    id: String(item.id || item.session_id || ""),
    current: item.current === true || item.is_current === true,
  })).filter((item) => item.id);
}

function normalizeSessionPage(payload) {
  const items = normalizeSessions(payload);
  return {
    items,
    page: Number(payload?.page || 1),
    page_size: Number(payload?.page_size || Math.max(items.length, 20)),
    total: Number(payload?.total ?? items.length),
  };
}

function sessionDevice(item) {
  const agent = String(item.user_agent || "").toLowerCase();
  return /mobile|iphone|android/.test(agent) ? "移动设备" : "桌面设备";
}

function accountError(error) {
  if (error?.code === "STEP_UP_REQUIRED") return "此安全操作需要重新验证身份，正在前往身份提供方。";
  return error?.message || "账号信息暂时无法读取，请稍后重试。";
}

function deactivationError(error) {
  if (error?.status === 409) {
    return "账号安全状态已变化，或当前账号仍承担平台所有者/企业老板职责。安全状态已刷新；如仍有所有权，请先在对应管理台完成交接再停用。";
  }
  return accountError(error);
}

export function AccountCenter({
  demoMode = false,
  demoIdentity = null,
  taskCompletionNotices,
  onTaskCompletionNoticesChange,
}) {
  const {
    client,
    session,
    beginLogin,
    logout,
    finishSession,
    refreshSession,
    handleAuthenticationError,
  } = useAuth();
  const [account, setAccount] = useState(() => normalizeAccount(null, session));
  const [sessions, setSessions] = useState([]);
  const [sessionPage, setSessionPage] = useState({ page: 1, page_size: 20, total: 0 });
  const [loading, setLoading] = useState(!demoMode);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [displayName, setDisplayName] = useState(account.display_name);
  const [deactivateOpen, setDeactivateOpen] = useState(false);
  const [deactivateConfirmation, setDeactivateConfirmation] = useState("");

  const accountManagementUrl = useMemo(
    () => safeAccountManagementUrl(session?.account_management_url),
    [session?.account_management_url],
  );

  const load = async ({ signal } = {}) => {
    if (demoMode || !client) return;
    setLoading(true);
    setError("");
    try {
      const [nextAccount, nextSessions] = await Promise.all([
        client.getAccount({ signal }),
        client.listSessions({ page: 1, pageSize: 20, signal }),
      ]);
      const normalized = normalizeAccount(nextAccount, session);
      setAccount(normalized);
      setDisplayName(normalized.display_name);
      const normalizedSessions = normalizeSessionPage(nextSessions);
      setSessions(normalizedSessions.items);
      setSessionPage(normalizedSessions);
    } catch (nextError) {
      if (nextError?.name !== "AbortError" && !handleAuthenticationError(nextError)) {
        setError(accountError(nextError));
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    if (demoMode) {
      const normalized = normalizeAccount(demoIdentity, { user: demoIdentity });
      setAccount(normalized);
      setDisplayName(normalized.display_name);
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    load({ signal: controller.signal });
    return () => controller.abort();
  }, [client, demoMode, demoIdentity?.user_id, session?.user?.id]);

  const saveProfile = async (event) => {
    event.preventDefault();
    const normalizedName = displayName.trim();
    if (!normalizedName) {
      setError("姓名不能为空。");
      return;
    }
    setBusy("profile");
    setError("");
    setNotice("");
    try {
      const updated = await client.updateAccount({
        displayName: normalizedName,
        expectedAuthVersion: account.auth_version,
        expectedUpdatedAt: account.updated_at,
      });
      const normalized = normalizeAccount(updated, session);
      setAccount(normalized);
      setDisplayName(normalized.display_name);
      await refreshSession({ silent: true });
      setNotice("账号资料已更新。");
    } catch (nextError) {
      if (!handleAuthenticationError(nextError)) {
        if (nextError?.status === 409) {
          await load();
          setError("资料已在另一个窗口更新。已重新读取最新内容，请确认后再次保存。");
        } else {
          setError(accountError(nextError));
        }
      }
    } finally {
      setBusy("");
    }
  };

  const revokeSession = async (item) => {
    setBusy(`session:${item.id}`);
    setError("");
    setNotice("");
    try {
      await client.revokeSession(item.id);
      if (item.current) {
        finishSession({
          to: "/login?logged_out=1",
          broadcastType: "session_revoked",
        });
        return;
      }
      setSessions((current) => current.filter((sessionItem) => sessionItem.id !== item.id));
      setSessionPage((current) => ({ ...current, total: Math.max(0, current.total - 1) }));
      setNotice("所选设备会话已撤销。");
    } catch (nextError) {
      if (!handleAuthenticationError(nextError)) setError(accountError(nextError));
    } finally {
      setBusy("");
    }
  };

  const loadMoreSessions = async () => {
    if (demoMode || !client || busy || sessions.length >= sessionPage.total) return;
    setBusy("sessions-more");
    setError("");
    try {
      const nextPage = normalizeSessionPage(await client.listSessions({
        page: sessionPage.page + 1,
        pageSize: sessionPage.page_size,
      }));
      setSessions((current) => {
        const merged = new Map(current.map((item) => [item.id, item]));
        nextPage.items.forEach((item) => merged.set(item.id, item));
        return [...merged.values()];
      });
      setSessionPage(nextPage);
    } catch (nextError) {
      if (!handleAuthenticationError(nextError)) setError(accountError(nextError));
    } finally {
      setBusy("");
    }
  };

  const revokeAll = async () => {
    setBusy("revoke-all");
    setError("");
    try {
      await client.revokeAllSessions();
      finishSession({
        to: "/login?logged_out=1",
        broadcastType: "revoke_all",
      });
    } catch (nextError) {
      if (!handleAuthenticationError(nextError)) setError(accountError(nextError));
    } finally {
      setBusy("");
    }
  };

  const deactivate = async () => {
    setBusy("deactivate");
    setError("");
    try {
      await client.deactivateAccount({ expectedAuthVersion: account.auth_version });
    } catch (nextError) {
      if (!handleAuthenticationError(nextError)) {
        if (nextError?.status === 409) await load();
        setError(deactivationError(nextError));
      }
      setBusy("");
      return;
    }
    finishSession({
      to: "/login?deactivated=1",
      broadcastType: "deactivated",
    });
  };

  if (loading) {
    return (
      <section className="secondary-view account-center" aria-labelledby="account-center-loading" aria-busy="true">
        <div className="account-state" role="status">
          <SpinnerGap className="is-spinning" size={24} aria-hidden="true" />
          <div><h1 id="account-center-loading">正在读取账号安全状态</h1><p>资料和设备会话确认完成前不会开放安全操作。</p></div>
        </div>
      </section>
    );
  }

  const visibleAccount = demoMode ? normalizeAccount(demoIdentity, { user: demoIdentity }) : account;
  const deactivationMatches = deactivateConfirmation.trim() === "DEACTIVATE";
  const hasAuthVersion = Number.isInteger(account.auth_version) && account.auth_version >= 0;
  const hasProfileSnapshot = Number.isInteger(account.auth_version) && Boolean(account.updated_at);

  return (
    <section className="secondary-view account-center" aria-labelledby="account-center-title">
      <header className="secondary-heading account-center-heading">
        <div>
          <span className="view-kicker">账号与安全</span>
          <h1 id="account-center-title">账号中心</h1>
          <p>管理个人资料、正式身份提供方和登录设备。企业权限与个人积分继续保持隔离。</p>
        </div>
        {!demoMode ? (
          <button className="account-quiet-button" type="button" onClick={() => load()} disabled={Boolean(busy)}>
            <ClockCounterClockwise size={17} aria-hidden="true" />刷新安全状态
          </button>
        ) : null}
      </header>

      {demoMode ? (
        <div className="account-banner" role="note">
          <WarningCircle size={19} aria-hidden="true" />演示账号不创建、修改或撤销任何真实身份与设备会话。
        </div>
      ) : null}
      {error ? <div className="account-banner is-error" role="alert">{error}</div> : null}
      {notice ? <div className="account-banner is-success" role="status"><Check size={17} aria-hidden="true" />{notice}</div> : null}

      <div className="account-sections">
        <section className="account-section" aria-labelledby="profile-title">
          <header><span><UserCircle size={21} aria-hidden="true" /></span><div><h2 id="profile-title">个人资料</h2><p>姓名会显示在任务、审计和协作记录中。</p></div></header>
          <form className="account-profile-form" onSubmit={saveProfile}>
            <label><span>姓名</span><input name="displayName" value={displayName} maxLength={120} autoComplete="name" disabled={demoMode || busy === "profile"} onChange={(event) => setDisplayName(event.target.value)} /></label>
            <label><span>登录邮箱</span><input value={visibleAccount.email} type="email" autoComplete="email" readOnly /></label>
            <div className="account-form-meta">
              <span className={visibleAccount.email_verified_at ? "is-verified" : ""}>
                {visibleAccount.email_verified_at ? "邮箱已验证" : "邮箱验证状态未提供"}
              </span>
              <button className="account-primary-button" type="submit" disabled={demoMode || busy === "profile" || !hasProfileSnapshot || displayName.trim() === visibleAccount.display_name}>
                {busy === "profile" ? <SpinnerGap className="is-spinning" size={17} aria-hidden="true" /> : null}保存资料
              </button>
            </div>
            {!hasProfileSnapshot && !demoMode ? <p className="account-inline-error" role="alert">资料版本尚未返回，刷新安全状态后才能保存。</p> : null}
          </form>
        </section>

        <section className="account-section" aria-labelledby="identity-provider-title">
          <header><span><LockKey size={21} aria-hidden="true" /></span><div><h2 id="identity-provider-title">登录方式</h2><p>密码、MFA、通行密钥和恢复方式全部由正式身份提供方管理。</p></div></header>
          <div className="identity-provider-row">
            <div><strong>企业身份提供方</strong><small>旭天不展示伪本地密码、MFA 或通行密钥设置。</small></div>
            {accountManagementUrl ? (
              <a className="account-quiet-button" href={accountManagementUrl} target="_blank" rel="noopener noreferrer">前往管理<ArrowSquareOut size={16} aria-hidden="true" /></a>
            ) : (
              <button className="account-quiet-button" type="button" disabled={demoMode} onClick={() => beginLogin({ returnTo: currentReturnTo(), prompt: "step_up" })}>重新验证身份</button>
            )}
          </div>
        </section>

        <section className="account-section" aria-labelledby="sessions-title">
          <header><span><Laptop size={21} aria-hidden="true" /></span><div><h2 id="sessions-title">登录设备</h2><p>撤销不再使用的会话。安全操作可能要求重新验证身份。</p></div></header>
          {demoMode ? (
            <div className="account-empty">演示模式不读取真实设备会话。</div>
          ) : sessions.length ? (
            <div className="account-session-list">
              {sessions.map((item) => (
                <article key={item.id} className={item.current ? "is-current" : ""}>
                  <span className="account-device-icon" aria-hidden="true">{sessionDevice(item) === "移动设备" ? <DeviceMobile size={20} /> : <Laptop size={20} />}</span>
                  <div><strong>{sessionDevice(item)}{item.current ? " · 当前设备" : ""}</strong><small>最近活动 {formatDateTime(item.last_seen_at)} · 到期 {formatDateTime(item.expires_at)}</small><small>{Array.isArray(item.amr) && item.amr.length ? `验证方式 ${item.amr.join(" · ")}` : "验证方式未提供"}</small></div>
                  <button className="account-quiet-button" type="button" disabled={Boolean(busy)} onClick={() => revokeSession(item)}>
                    {busy === `session:${item.id}` ? <SpinnerGap className="is-spinning" size={16} aria-hidden="true" /> : <SignOut size={16} aria-hidden="true" />}
                    {item.current ? "退出" : "撤销"}
                  </button>
                </article>
              ))}
            </div>
          ) : <div className="account-empty">当前没有可显示的设备会话。</div>}
          {!demoMode && sessions.length < sessionPage.total ? (
            <button className="account-quiet-button account-load-more" type="button" disabled={Boolean(busy)} onClick={loadMoreSessions}>
              {busy === "sessions-more" ? <SpinnerGap className="is-spinning" size={16} aria-hidden="true" /> : null}
              加载更多设备（{sessions.length}/{sessionPage.total}）
            </button>
          ) : null}
          <div className="account-section-actions">
            <button className="account-danger-quiet" type="button" disabled={demoMode || Boolean(busy)} onClick={revokeAll}>退出所有设备</button>
            <button className="account-quiet-button" type="button" disabled={demoMode || Boolean(busy)} onClick={() => logout()}><SignOut size={16} aria-hidden="true" />退出当前账号</button>
          </div>
        </section>

        <section className="account-section" aria-labelledby="preferences-title">
          <header><span><Check size={21} aria-hidden="true" /></span><div><h2 id="preferences-title">工作台偏好</h2><p>偏好仅保存在当前浏览器，不改变权限、计费或归档策略。</p></div></header>
          <label className="account-toggle-row">
            <span><strong>任务结果站内提示</strong><small>任务完成、失败、超时或需要人工确认时显示站内提示。</small></span>
            <input type="checkbox" checked={taskCompletionNotices} onChange={(event) => onTaskCompletionNoticesChange(event.target.checked)} />
          </label>
        </section>

        <section className="account-section is-danger-zone" aria-labelledby="deactivate-title">
          <header><span><Trash size={21} aria-hidden="true" /></span><div><h2 id="deactivate-title">停用账号</h2><p>停止自然人账号的新访问；历史任务、账务与审计记录仍按平台规则保留。</p></div></header>
          {!deactivateOpen ? (
            <button className="account-danger-quiet" type="button" disabled={demoMode || Boolean(busy)} onClick={() => setDeactivateOpen(true)}>了解并停用账号</button>
          ) : (
            <div className="account-deactivate-confirm" role="group" aria-labelledby="deactivate-confirm-title">
              <strong id="deactivate-confirm-title">确认停用账号</strong>
              <p>输入 <b>DEACTIVATE</b> 以确认。平台所有者或企业最后一位老板必须先完成职责交接。</p>
              {Array.isArray(session?.companies) && session.companies.length ? (
                <div className="account-handoff-actions">
                  <button className="account-quiet-button" type="button" onClick={() => globalThis.location?.assign?.("/company")}>前往企业成员管理完成交接</button>
                </div>
              ) : null}
              {session?.platform_admin ? <p>若此账号位于平台所有者服务端允许列表，还需先由部署管理员完成所有者主体变更。</p> : null}
              <label><span>确认文案</span><input type="text" autoComplete="off" spellCheck="false" value={deactivateConfirmation} onChange={(event) => setDeactivateConfirmation(event.target.value)} /></label>
              {!hasAuthVersion && !demoMode ? <p className="account-inline-error" role="alert">账号版本尚未返回，刷新安全状态后才能停用。</p> : null}
              <div><button className="account-quiet-button" type="button" onClick={() => { setDeactivateOpen(false); setDeactivateConfirmation(""); }}>取消</button><button className="account-danger-button" type="button" disabled={!deactivationMatches || !hasAuthVersion || busy === "deactivate"} onClick={deactivate}>{busy === "deactivate" ? <SpinnerGap className="is-spinning" size={17} aria-hidden="true" /> : null}确认停用</button></div>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
