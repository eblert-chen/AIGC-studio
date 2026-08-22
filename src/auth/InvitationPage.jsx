import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle,
  SpinnerGap,
  UsersThree,
} from "@phosphor-icons/react";
import { AuthShell } from "./AuthShell.jsx";
import { clearInvitationToken } from "./authClient.js";

const ROLE_LABELS = {
  owner: "老板",
  team_lead: "组长",
  operator: "运营",
};

function invitationState(status) {
  if (["accepted", "used"].includes(status)) return { title: "这份邀请已经使用", detail: "无需重复接受，可直接进入工作台。", tone: "success" };
  if (status === "expired") return { title: "这份邀请已过期", detail: "请联系企业老板重新发送邀请。", tone: "warning" };
  if (status === "revoked") return { title: "这份邀请已撤销", detail: "邀请已被企业管理员撤销，无法继续使用。", tone: "warning" };
  return { title: "加入企业工作空间", detail: "接受后，你的个人空间与企业钱包、权限和数据仍会分别管理。", tone: "secure" };
}

function formatExpiry(value) {
  if (!value) return "未提供";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function errorCopy(error) {
  if (error?.code === "invitation_invalid" || error?.status === 404) return "邀请不存在或链接无效。请确认链接完整，或联系邀请人重新发送。";
  if (error?.code === "invitation_expired" || error?.status === 410) return "邀请已经过期，请联系企业老板重新发送。";
  if (error?.code === "invitation_used") return "邀请已经使用，无需重复接受。";
  if (error?.code === "invitation_revoked") return "邀请已经撤销，请联系企业老板重新发送。";
  if (error?.code === "invitation_email_mismatch") return "当前账号不是受邀邮箱，请先安全退出并换用受邀账号登录。";
  if (error?.code === "invitation_account_unavailable") return "当前账号暂不可接受邀请，请联系平台管理员恢复账号状态。";
  if (error?.code === "invitation_company_unavailable") return "受邀企业当前不可用，请联系平台管理员恢复企业状态。";
  if (error?.code === "invitation_membership_conflict") return "当前账号在该企业已有不兼容的成员关系，请联系企业老板处理。";
  if (error?.status === 409) return "邀请当前不可用，可能已经接受、被撤销，或成员关系已变化。";
  return error?.message || "无法读取邀请，请稍后重试。";
}

export function InvitationPage({
  token,
  client,
  session,
  authStatus,
  onPreviewEstablished,
  onLogin,
  onSwitchAccount,
  onAbandon,
  onAccepted,
  onAuthenticationError,
}) {
  const [invitation, setInvitation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState("");
  const [accepted, setAccepted] = useState(false);
  const handoffEstablishedRef = useRef(false);

  const load = () => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    client.getInvitation(token, { signal: controller.signal })
      .then((result) => {
        setInvitation(result);
        if (token) {
          handoffEstablishedRef.current = true;
          onPreviewEstablished?.();
        }
      })
      .catch((nextError) => {
        if (nextError?.name !== "AbortError") setError(errorCopy(nextError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return controller;
  };

  useEffect(() => {
    if (!token && handoffEstablishedRef.current) return undefined;
    handoffEstablishedRef.current = false;
    setInvitation(null);
    setAccepted(false);
    setAccepting(false);
    const controller = load();
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (["accepted", "used", "revoked"].includes(invitation?.status)) clearInvitationToken();
  }, [invitation?.status]);

  const state = invitationState(invitation?.status);
  const signedInEmail = String(session?.user?.email || "").trim().toLowerCase();
  const invitedEmail = String(invitation?.email || "").trim().toLowerCase();
  const emailMismatch = Boolean(
    session?.authenticated && signedInEmail && invitedEmail && signedInEmail !== invitedEmail,
  );
  const pending = invitation?.status === "pending";
  const returnTo = useMemo(() => "/invite", []);

  const accept = async () => {
    if (!session?.authenticated) {
      onLogin({ returnTo, prompt: "login" });
      return;
    }
    if (emailMismatch) {
      await onSwitchAccount();
      return;
    }
    setAccepting(true);
    setError("");
    try {
      const result = await client.acceptInvitation();
      clearInvitationToken();
      setAccepted(true);
      setInvitation((current) => ({ ...current, ...result, status: "accepted" }));
    } catch (nextError) {
      if (!onAuthenticationError(nextError)) setError(errorCopy(nextError));
    } finally {
      setAccepting(false);
    }
  };

  if (loading) {
    return (
      <AuthShell
        eyebrow="企业邀请"
        title="正在核验邀请"
        description="系统正在确认邀请所属企业、受邀邮箱与有效期。"
        tone="loading"
        busy
      >
        <div className="auth-inline-loading" role="status">
          <SpinnerGap className="is-spinning" size={20} aria-hidden="true" />
          正在读取邀请信息…
        </div>
      </AuthShell>
    );
  }

  if (error && !invitation) {
    return (
      <AuthShell eyebrow="企业邀请" title="无法打开邀请" description={error} tone="warning">
        <button className="auth-secondary-action" type="button" onClick={load}>重新检查邀请</button>
      </AuthShell>
    );
  }

  const completed = accepted || ["accepted", "used"].includes(invitation?.status);
  return (
    <AuthShell
      eyebrow="企业邀请"
      title={completed ? "已加入企业" : state.title}
      description={completed ? "企业成员关系已创建，服务端权限会在进入工作台时重新加载。" : state.detail}
      tone={completed ? "success" : state.tone}
      busy={accepting}
      footer="邀请只绑定受邀邮箱；接受邀请不会合并个人积分与企业共享钱包。"
    >
      <div className="invitation-summary">
        <span className="invitation-company-mark" aria-hidden="true"><UsersThree size={22} /></span>
        <div><small>受邀加入</small><strong>{invitation?.company_name || "企业工作空间"}</strong></div>
      </div>
      <dl className="auth-detail-list">
        <div><dt>受邀邮箱</dt><dd>{invitation?.email || "未提供"}</dd></div>
        <div><dt>初始级别</dt><dd>{ROLE_LABELS[invitation?.primary_role] || invitation?.primary_role || "运营"}</dd></div>
        <div><dt>邀请人</dt><dd>{invitation?.inviter_name || "企业管理员"}</dd></div>
        <div><dt>有效期至</dt><dd>{formatExpiry(invitation?.expires_at)}</dd></div>
      </dl>
      {emailMismatch ? (
        <div className="auth-alert" role="alert">
          当前登录账号 {session.user.email} 与受邀邮箱不一致。切换账号会先安全退出当前会话，再要求身份提供方明确选择账号。
        </div>
      ) : null}
      {error ? <div className="auth-alert" role="alert">{error}</div> : null}
      {completed ? (
        <button className="auth-primary-action" type="button" onClick={() => onAccepted(invitation)}>
          <CheckCircle size={18} weight="fill" aria-hidden="true" />进入工作台<ArrowRight size={18} aria-hidden="true" />
        </button>
      ) : pending ? (
        <button className="auth-primary-action" type="button" disabled={accepting || authStatus === "loading"} onClick={accept}>
          {accepting ? <SpinnerGap className="is-spinning" size={18} aria-hidden="true" /> : null}
          {!session?.authenticated
            ? "登录受邀账号并继续"
            : emailMismatch
              ? "换用受邀邮箱登录"
              : accepting ? "正在加入企业" : "接受邀请"}
          {!accepting ? <ArrowRight size={18} aria-hidden="true" /> : null}
        </button>
      ) : (
        <button className="auth-secondary-action" type="button" onClick={onAbandon}>
          结束邀请流程
        </button>
      )}
      {!completed && pending ? (
        <button className="auth-tertiary-action" type="button" disabled={accepting || authStatus === "navigating"} onClick={onAbandon}>
          暂不加入并清除邀请
        </button>
      ) : null}
    </AuthShell>
  );
}
