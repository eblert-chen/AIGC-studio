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
  variant = "full",
  children,
  footer,
}) {
  const [skin, setSkin] = useSkinPreference();
  const headingId = useId();
  const Icon = ICONS[tone] || LockKey;
  const stateIconIsLoading = tone === "loading";
  const loginVariant = variant === "login";

  return (
    <main
      className={`auth-shell is-${tone} ${loginVariant ? "is-login" : "is-full"}`}
      data-theme={skin}
      aria-labelledby={headingId}
      aria-busy={busy || undefined}
    >
      {!loginVariant ? (
        <header className="auth-topbar">
          <div className="auth-brand" aria-label={BRAND_NAME}>
            <BrandLogo variant="responsive" mobileBreakpoint={620} />
            <span>统一身份入口</span>
          </div>
          <div className="auth-topbar-tools">
            <span className="auth-boundary-label">服务端身份边界</span>
            <SkinSwitcher value={skin} onChange={setSkin} />
          </div>
        </header>
      ) : null}

      <div className="auth-workspace">
        <section className="auth-panel">
          {loginVariant ? (
            <div className="auth-login-brand">
              <BrandLogo variant="responsive" mobileBreakpoint={420} />
            </div>
          ) : (
            <>
              <div className="auth-panel-register" aria-hidden="true">
                <span>IDENTITY / ACCESS</span>
                <span>SERVER AUTHORITATIVE</span>
              </div>
              <div className="auth-panel-heading" role="status">
                <span className={`auth-state-icon ${stateIconIsLoading ? "is-spinning" : ""}`} aria-hidden="true">
                  <Icon size={22} weight={tone === "success" ? "fill" : "bold"} />
                </span>
                <span className="auth-context-label"><small>访问状态</small><strong>{eyebrow}</strong></span>
              </div>
            </>
          )}
          {loginVariant && stateIconIsLoading ? (
            <div className="auth-login-progress" role="status">
              <SpinnerGap className="is-spinning" size={22} aria-hidden="true" />
              <span className="visually-hidden">{eyebrow || "正在处理账号状态"}</span>
            </div>
          ) : null}
          <h1 id={headingId}>{title}</h1>
          {description ? <p className="auth-description">{description}</p> : null}
          <div className="auth-panel-content">{children}</div>
          {footer ? <footer className="auth-panel-footer">{footer}</footer> : null}
        </section>

        {!loginVariant ? (
          <aside className="auth-assurance" aria-label="账号安全说明">
            <div className="auth-assurance-heading">
              <span>访问边界</span>
              <h2>创作权限，从登录这一刻就分开。</h2>
              <p>每个产品账号只进入个人、企业或平台中的一个边界；个人积分、企业钱包、任务和管理权限不会合并。</p>
            </div>
            <ul className="auth-trust-path" aria-label="安全会话建立过程">
              <li><span className="auth-trust-index">01</span><CheckCircle size={18} weight="bold" aria-hidden="true" /><span>身份验证</span><small>由正式身份提供方确认账号与强认证状态</small></li>
              <li><span className="auth-trust-index">02</span><CheckCircle size={18} weight="bold" aria-hidden="true" /><span>范围裁决</span><small>服务端只返回当前账号获准进入的工作空间</small></li>
              <li><span className="auth-trust-index">03</span><CheckCircle size={18} weight="bold" aria-hidden="true" /><span>权限过滤</span><small>页面可见性不替代每一次接口鉴权</small></li>
            </ul>
            <dl aria-label="会话安全契约">
              <div><dt>会话</dt><dd>HttpOnly 安全 Cookie</dd></div>
              <div><dt>写操作</dt><dd>同源校验与 CSRF 防护</dd></div>
              <div><dt>身份</dt><dd>由正式身份提供方验证</dd></div>
            </dl>
            <p className="auth-assurance-note">身份或权限证据读取失败时保持关闭，不回退到演示身份或历史权限。</p>
          </aside>
        ) : null}
      </div>
    </main>
  );
}
