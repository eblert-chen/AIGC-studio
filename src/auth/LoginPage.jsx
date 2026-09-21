import {
  ArrowClockwise,
  ArrowRight,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { AuthShell } from "./AuthShell.jsx";

export function LoginPage({
  onLogin,
  onRetry,
  returnTo = "/",
  error = "",
  loggedOut = false,
  deactivated = false,
  busy = false,
}) {
  const state = error
    ? "error"
    : deactivated
      ? "deactivated"
      : loggedOut
        ? "logged-out"
        : "ready";
  const retryingUnavailableService = state === "error";
  const title = state === "error"
    ? "账号服务暂不可用"
    : state === "deactivated"
      ? "这个账号已停用"
      : state === "logged-out"
        ? "已安全退出"
        : "登录旭天";
  const description = state === "error"
    ? "重新检测只会确认连接状态，不会创建会话或进入任何工作区。"
    : state === "deactivated"
      ? "当前账号不能再进入工作区，你仍可以使用其他账号登录。"
      : state === "logged-out"
        ? "本机已结束当前会话，需要时可以重新进入个人或企业工作区。"
        : "个人用户和企业成员都从这里进入，下一步由安全身份服务完成验证。";
  const actionLabel = busy
    ? "正在打开安全登录"
    : retryingUnavailableService
      ? "重新检测账号服务"
      : state === "deactivated"
        ? "使用其他账号登录"
        : state === "logged-out"
          ? "重新登录"
          : "继续登录";
  const actionUnavailable = retryingUnavailableService
    ? typeof onRetry !== "function"
    : typeof onLogin !== "function";

  return (
    <AuthShell
      variant="login"
      title={title}
      description={description}
      tone={retryingUnavailableService || state === "deactivated" ? "warning" : state === "logged-out" ? "success" : "secure"}
      busy={busy}
      footer="密码与验证码由安全身份服务处理，旭天不会保存。"
    >
      {retryingUnavailableService ? (
        <div className="auth-login-state" role="alert">
          <WarningCircle size={19} weight="bold" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}
      {busy ? <p className="visually-hidden" role="status">正在打开安全登录页面</p> : null}
      <button
        className="auth-primary-action"
        type="button"
        data-action={retryingUnavailableService ? "retry" : "login"}
        disabled={busy || actionUnavailable}
        onClick={() => {
          if (retryingUnavailableService) onRetry?.();
          else onLogin?.({
            returnTo,
            prompt: state === "deactivated" ? "select_account" : "login",
          });
        }}
      >
        <span>{actionLabel}</span>
        {busy ? <SpinnerGap className="is-spinning" size={18} aria-hidden="true" /> : null}
        {!busy && retryingUnavailableService ? <ArrowClockwise size={18} aria-hidden="true" /> : null}
        {!busy && !retryingUnavailableService ? <ArrowRight size={18} aria-hidden="true" /> : null}
      </button>
    </AuthShell>
  );
}
