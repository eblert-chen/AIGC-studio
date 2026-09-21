import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

async function source(path) {
  return readFile(new URL(`../${path}`, import.meta.url), "utf8");
}

async function platformApiSource() {
  const files = [
    "platformClient.js",
    "platformCore.js",
    "sessionPersonalApi.js",
    "companyApi.js",
    "publishingApi.js",
    "platformAdminApi.js",
    "assetsTasksApi.js",
  ];
  return (await Promise.all(files.map((file) => source(`src/api/${file}`)))).join("\n");
}

test("authentication routes expose explicit loading, error and keyboard contracts", async () => {
  const [shell, login, invitation, callback, styles] = await Promise.all([
    source("src/auth/AuthShell.jsx"),
    source("src/auth/LoginPage.jsx"),
    source("src/auth/InvitationPage.jsx"),
    source("src/auth/AuthCallbackPage.jsx"),
    source("src/design-system/auth.css"),
  ]);

  assert.match(shell, /<BrandLogo variant="responsive"/);
  assert.match(shell, /aria-busy/);
  assert.match(shell, /variant = "full"/);
  assert.match(shell, /const loginVariant = variant === "login"/);
  assert.match(shell, /!loginVariant \? \(/);
  assert.match(shell, /className="auth-login-brand"/);
  assert.match(shell, /const stateIconIsLoading = tone === "loading"/);
  assert.match(shell, /stateIconIsLoading \? "is-spinning" : ""/);
  assert.doesNotMatch(shell, /auth-state-icon \$\{busy \? "is-spinning"/);
  assert.match(shell, /<ul className="auth-trust-path"/);
  assert.doesNotMatch(shell, /<ol className="auth-trust-path"/);
  assert.match(shell, /每个产品账号只进入个人、企业或平台中的一个边界/);
  assert.doesNotMatch(shell, /一个自然人身份可以进入个人、企业与平台工作区/);
  assert.doesNotMatch(login, /autoFocus/);
  assert.match(login, /role="alert"/);
  assert.match(login, /variant="login"/);
  assert.match(login, /登录旭天/);
  assert.match(login, /个人用户和企业成员都从这里进入/);
  assert.match(login, /继续登录/);
  assert.match(login, /旭天不会保存/);
  assert.match(login, /onRetry\?\.\(\)/);
  assert.match(login, /className="auth-login-state"/);
  assert.match(login, /prompt: state === "deactivated" \? "select_account" : "login"/);
  assert.doesNotMatch(login, /!deactivated \? \(/);
  assert.doesNotMatch(login, /auth-scope-ledger|auth-secondary-copy|身份凭据|返回路径|授权范围/);
  assert.match(callback, /onRetry/);
  assert.match(invitation, /const returnTo = useMemo\(\(\) => "\/invite"/);
  assert.match(invitation, /onLogin\(\{ returnTo, prompt: "login" \}\)/);
  assert.match(invitation, /await onSwitchAccount\(\)/);
  assert.doesNotMatch(invitation, /returnTo:[^\n]*token|\/invitations\/\$\{/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /:-webkit-autofill/);
  assert.match(styles, /@media \(max-width: 390px\)/);
  assert.match(styles, /@media \(max-width: 340px\)/);
  assert.match(styles, /@media \(max-width: 420px\)[\s\S]*?\.auth-login-brand \.brand-logo--responsive \{ width:\s*46px; height:\s*46px; flex-basis:\s*46px; \}/);
  assert.match(styles, /--auth-cobalt:\s*var\(--selection-violet\)/);
  assert.match(styles, /--auth-orange:\s*var\(--workflow-orange\)/);
  assert.match(styles, /\.auth-shell\.is-login\s*\{[\s\S]*?grid-template-rows:\s*minmax\(0, 1fr\)/);
  assert.match(styles, /\.auth-shell\.is-login \.auth-workspace\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(styles, /\.auth-shell\.is-login \.auth-panel\s*\{[\s\S]*?width:\s*min\(100%, 480px\)/);
  assert.match(styles, /\.auth-shell\.is-login \.auth-primary-action:disabled\s*\{[\s\S]*?opacity:\s*1/);
  assert.doesNotMatch(styles, /\.auth-scope-ledger|\.auth-secondary-copy/);
  assert.match(styles, /\.auth-shell \.skin-switcher\s*\{[\s\S]*?min-height:\s*var\(--control-md\)/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*?\.auth-shell \.skin-switcher,[\s\S]*?\.auth-shell \.skin-switcher-trigger\s*\{[^}]*min-height:\s*var\(--control-lg\)/);
  assert.doesNotMatch(styles, /\.auth-shell \.skin-switcher[^\n{]*select/);
});

test("account center delegates credentials to the IdP and requires versioned typed deactivation", async () => {
  const account = await source("src/AccountCenter.jsx");

  assert.match(account, /密码、验证方式和账号恢复由你的组织登录服务管理/);
  assert.match(account, /authenticationMethodSummary\(item\.amr\)/);
  assert.doesNotMatch(account, /item\.amr\.join/);
  assert.doesNotMatch(account, /type="password"/);
  assert.match(account, /deactivateConfirmation\.trim\(\) === "DEACTIVATE"/);
  assert.match(account, /expectedAuthVersion:\s*account\.auth_version/);
  assert.match(account, /expectedUpdatedAt:\s*account\.updated_at/);
  assert.match(account, /Number\.isInteger\(account\.auth_version\)/);
  assert.match(account, /status === 409/);
  assert.match(account, /完成交接/);
  assert.match(account, /查看停用说明/);
  assert.match(account, /停用账号并退出/);
});

test("transient session errors preserve local work while explicit invalidation syncs tabs", async () => {
  const [gateway, sync] = await Promise.all([
    source("src/auth/AuthGateway.jsx"),
    source("src/auth/sessionSync.js"),
  ]);

  assert.match(gateway, /const explicitlyUnauthenticated/);
  assert.match(gateway, /isAuthUnavailableError/);
  assert.match(gateway, /nextSession\.login_available === true/);
  assert.match(gateway, /setLoginAvailable\(false\)/);
  assert.match(gateway, /setStatus\(\(current\) => current === "authenticated" \? current : "error"\)/);
  assert.match(gateway, /createAuthSessionSync/);
  assert.match(gateway, /refreshSession\(\{ broadcast: false \}\)/);
  assert.match(gateway, /prompt: "select_account"/);
  assert.match(gateway, /const switchAccount = useCallback/);
  assert.match(gateway, /kind: "switch_account"/);
  assert.match(gateway, /returnTo: safeReturnTo\(returnTo\)/);
  assert.match(gateway, /`\/login\?return_to=\$\{encodeURIComponent\(switchReturnTo\)\}`/);
  assert.match(gateway, /logout_uncertain/);
  assert.match(gateway, /重试并确认服务端退出/);
  assert.match(gateway, /nextError instanceof PlatformApiError && nextError\.status === 401/);
  assert.match(gateway, /preserveInvitation: intent\?\.kind === "switch_invitation_account"/);
  assert.match(gateway, /const switchProductContext = useCallback/);
  assert.match(gateway, /client\.switchProductContext\(\{ targetContext \}\)/);
  assert.match(gateway, /publishSessionEvent\("product_context_changed"/);
  assert.match(sync, /"product_context_changed"/);
  assert.doesNotMatch(gateway, /client\?\.logout\(\);[\s\S]{0,200}finally/);
  assert.match(gateway, /establishInvitationHandoff/);
  assert.match(sync, /BroadcastChannel/);
  assert.match(sync, /AUTH_SESSION_STORAGE_KEY/);
  assert.doesNotMatch(sync, /access-token|pending-invitation-token/);
});

test("browser lifecycle revalidates restored sessions and renders truthful callback and deactivation states", async () => {
  const [gateway, callback, login] = await Promise.all([
    source("src/auth/AuthGateway.jsx"),
    source("src/auth/AuthCallbackPage.jsx"),
    source("src/auth/LoginPage.jsx"),
  ]);

  assert.match(gateway, /addEventListener\?\.\("pageshow", refreshAfterBackForwardCache\)/);
  assert.match(gateway, /removeEventListener\?\.\("pageshow", refreshAfterBackForwardCache\)/);
  assert.match(gateway, /if \(event\?\.persisted === true\) refreshSession\(\{ silent: true \}\)/);
  assert.match(gateway, /const LOGIN_NAVIGATION_TIMEOUT_MS = 8_000/);
  assert.match(gateway, /if \(status !== "navigating"\) return undefined/);
  assert.match(gateway, /setTimeout\?\.\(recoverNavigation, LOGIN_NAVIGATION_TIMEOUT_MS\)/);
  assert.match(gateway, /removeEventListener\?\.\("pageshow", recoverAfterBackForwardCache\)/);
  assert.match(gateway, /loginNavigationRef\.current/);
  assert.match(gateway, /deactivated=\{query\.get\("deactivated"\) === "1"\}/);
  assert.match(callback, /if \(status === "loading"\)/);
  assert.match(callback, /title="正在确认安全会话"[\s\S]*?tone="loading"[\s\S]*?busy/);
  assert.match(login, /deactivated = false/);
  assert.match(login, /这个账号已停用/);
  assert.match(login, /使用其他账号登录/);
  assert.match(login, /已安全退出/);
  assert.match(login, /const state = error/);
  assert.match(login, /retryingUnavailableService \|\| state === "deactivated"/);
  assert.match(login, /retryingUnavailableService/);
  assert.match(login, /if \(retryingUnavailableService\) onRetry\?\.\(\)/);
  assert.match(gateway, /error="账号服务地址无效，请联系管理员。"[\s\S]*?onRetry=\{\(\) => globalThis\.location\?\.reload\?\.\(\)\}/);
});

test("invitation capabilities are not embedded in frontend API paths or error copy", async () => {
  const [client, page] = await Promise.all([
    source("src/auth/authClient.js"),
    source("src/auth/InvitationPage.jsx"),
  ]);

  assert.match(client, /"\/api\/v1\/invitations\/preview"/);
  assert.match(client, /"\/api\/v1\/invitations\/accept"/);
  assert.doesNotMatch(client, /api\/v1\/invitations\/\$\{|searchParams\.set\("token"/);
  assert.doesNotMatch(client, /setItem\("ai-video\.pending-invitation-token"/);
  assert.match(client, /acceptInvitation: async \(\{ signal \} = \{\}\)/);
  assert.match(page, /client\.acceptInvitation\(\)/);
  assert.match(page, /error\?\.code === "account_type_conflict"/);
  assert.match(page, /个人消费者账号不能[^。]*加入企业/);
  assert.match(page, /(?:改用其他邮箱|使用另一个邮箱)/);
  assert.doesNotMatch(
    page,
    /接受后，你的个人空间与企业钱包、权限和数据仍会分别管理|接受邀请不会合并个人积分与企业共享钱包/,
  );
  assert.doesNotMatch(page, /\{token\}|token=|邀请令牌[^。]*\$\{/);
});

test("protected management creates invitations, transfers ownership and manages global account state", async () => {
  const [client, adminContainer] = await Promise.all([
    platformApiSource(),
    source("src/admin/AdminOperationsContainer.jsx"),
  ]);
  const management = managementSource;

  assert.match(management, /client\.createInvitation\(payload\)/);
  assert.doesNotMatch(management, /client\.createMember\(/);
  assert.match(managementSource, /pending/);
  assert.match(managementSource, /accepted/);
  assert.match(managementSource, /expired/);
  assert.match(managementSource, /revoked/);
  assert.match(management, /复制一次性链接/);
  assert.match(management, /url\.hash\.startsWith\("#token="\)/);
  assert.match(management, /client\.transferCompanyOwner/);
  assert.match(management, /expectedCurrentOwnerMembershipId:\s*data\.me\.membership_id/);
  assert.match(management, /client\.listPlatformUsers/);
  assert.match(management, /client\.setPlatformUserStatus/);
  assert.match(management, /normalizePageCollection/);
  assert.match(management, /company-invitations/);
  assert.match(management, /platform-users/);
  assert.match(management, /client\.reissueAdminCompanyOwnerInvitation/);
  assert.match(management, /owner_activation_required/);
  assert.match(management, /replacementEmail/);
  assert.match(management, /替换老板邮箱/);
  assert.doesNotMatch(management, /\{ownerInvitationLinks\[[^\]]+\]\}/);
  assert.match(management, /onSessionError\?\.\(mutationError\)/);
  assert.match(client, /"\/api\/v1\/platform-admin\/users"/);
  assert.match(client, /expected_auth_version:\s*expectedAuthVersion/);
  assert.match(adminContainer, /is_platform_owner\) return "users"/);
});
