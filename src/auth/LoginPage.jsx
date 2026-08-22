import { ArrowRight, SpinnerGap } from "@phosphor-icons/react";
import { AuthShell } from "./AuthShell.jsx";

export function LoginPage({
  onLogin,
  returnTo = "/",
  error = "",
  loggedOut = false,
  deactivated = false,
  busy = false,
}) {
  const eyebrow = deactivated
    ? "账号已停用"
    : loggedOut ? "会话已安全结束" : "正式账号登录";
  const title = deactivated
    ? "账号访问已停止"
    : loggedOut ? "已退出旭天 AI VIDEO" : "继续进入创作空间";
  const description = deactivated
    ? "当前自然人账号已停用，全部设备会话已经撤销。历史任务、账务与审计记录仍按平台规则保留；如需恢复，请联系平台管理员。"
    : loggedOut
      ? "本机工作区草稿和会话状态已清理。再次登录时会重新读取服务端权限。"
      : "使用已获授权的个人、企业或平台管理员身份继续。登录完成后，系统会安全返回当前页面。";

  return (
    <AuthShell
      eyebrow={eyebrow}
      title={title}
      description={description}
      tone={error || deactivated ? "warning" : "secure"}
      busy={busy}
      footer="密码、MFA 与通行密钥由正式身份提供方管理，旭天不会在此页面读取或保存它们。"
    >
      {error ? <div className="auth-alert" role="alert">{error}</div> : null}
      <button
        className="auth-primary-action"
        type="button"
        autoFocus
        disabled={busy}
        onClick={() => onLogin({ returnTo, prompt: "login" })}
      >
        {busy ? <SpinnerGap className="is-spinning" size={18} aria-hidden="true" /> : null}
        {busy ? "正在前往身份提供方" : "使用正式账号登录"}
        {!busy ? <ArrowRight size={18} aria-hidden="true" /> : null}
      </button>
      <p className="auth-secondary-copy">
        登录后只会显示当前账号被授权的个人空间、企业和管理模块。
      </p>
    </AuthShell>
  );
}
