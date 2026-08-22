import { useId } from "react";
import {
  CheckCircle,
  LockKey,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { BrandLogo, BRAND_NAME } from "../BrandLogo.jsx";
import { SkinSwitcher, useSkinPreference } from "../SkinSwitcher.jsx";

const ICONS = {
  loading: SpinnerGap,
  success: CheckCircle,
  warning: WarningCircle,
  secure: LockKey,
};

export function AuthShell({
  eyebrow = "安全访问",
  title,
  description,
  tone = "secure",
  busy = false,
  children,
  footer,
}) {
  const [skin, setSkin] = useSkinPreference();
  const headingId = useId();
  const Icon = ICONS[tone] || LockKey;

  return (
    <main
      className={`auth-shell is-${tone}`}
      data-theme={skin}
      aria-labelledby={headingId}
      aria-busy={busy || undefined}
    >
      <header className="auth-topbar">
        <div className="auth-brand" aria-label={BRAND_NAME}>
          <BrandLogo variant="responsive" mobileBreakpoint={620} />
          <span>统一身份入口</span>
        </div>
        <SkinSwitcher value={skin} onChange={setSkin} />
      </header>

      <div className="auth-workspace">
        <aside className="auth-assurance" aria-label="账号安全说明">
          <h2>创作权限，从登录这一刻就分开。</h2>
          <p>一个自然人身份可以进入个人、企业与平台工作区，但积分、钱包、任务和管理权限不会混在一起。</p>
          <ol className="auth-trust-path" aria-label="安全会话建立过程">
            <li><span>身份验证</span><small>由正式身份提供方确认账号与强认证状态</small></li>
            <li><span>范围裁决</span><small>服务端只返回当前账号获准进入的工作空间</small></li>
            <li><span>权限过滤</span><small>页面可见性不替代每一次接口鉴权</small></li>
          </ol>
          <dl>
            <div><dt>会话</dt><dd>HttpOnly 安全 Cookie</dd></div>
            <div><dt>写操作</dt><dd>同源校验与 CSRF 防护</dd></div>
            <div><dt>身份</dt><dd>由正式身份提供方验证</dd></div>
          </dl>
        </aside>

        <section className="auth-panel">
          <div className="auth-panel-heading">
            <span className={`auth-state-icon ${busy ? "is-spinning" : ""}`} aria-hidden="true">
              <Icon size={22} weight={tone === "success" ? "fill" : "bold"} />
            </span>
            <span className="auth-context-label">{eyebrow}</span>
          </div>
          <h1 id={headingId}>{title}</h1>
          {description ? <p className="auth-description">{description}</p> : null}
          <div className="auth-panel-content">{children}</div>
          {footer ? <footer className="auth-panel-footer">{footer}</footer> : null}
        </section>
      </div>
    </main>
  );
}
