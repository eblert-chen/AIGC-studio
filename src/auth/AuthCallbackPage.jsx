import { useEffect } from "react";
import { AuthShell } from "./AuthShell.jsx";
import { safeReturnTo } from "./authClient.js";

export function AuthCallbackPage({ session, status, onRetry }) {
  const params = new URLSearchParams(globalThis.location?.search || "");
  const returnTo = safeReturnTo(params.get("return_to") || "/");

  useEffect(() => {
    if (!session?.authenticated) return;
    globalThis.location?.replace?.(returnTo);
  }, [returnTo, session?.authenticated]);

  if (session?.authenticated) {
    return (
      <AuthShell eyebrow="登录完成" title="正在返回工作台" description="安全会话已建立，正在恢复你刚才访问的页面。" tone="success" busy />
    );
  }

  if (status === "loading") {
    return (
      <AuthShell
        eyebrow="登录回调"
        title="正在确认安全会话"
        description="正在验证身份提供方返回的结果，确认完成前不会开放任何账号数据。"
        tone="loading"
        busy
      />
    );
  }

  return (
    <AuthShell
      eyebrow="登录回调"
      title="登录尚未完成"
      description={status === "error" ? "无法确认新的登录会话，请重新检查。" : "身份提供方尚未建立可用会话。"}
      tone="warning"
    >
      <button className="auth-secondary-action" type="button" onClick={onRetry}>重新确认会话</button>
    </AuthShell>
  );
}
