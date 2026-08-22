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
  assert.match(login, /autoFocus/);
  assert.match(login, /role="alert"/);
  assert.match(callback, /onRetry/);
  assert.match(invitation, /const returnTo = useMemo\(\(\) => "\/invite"/);
  assert.match(invitation, /onLogin\(\{ returnTo, prompt: "login" \}\)/);
  assert.match(invitation, /await onSwitchAccount\(\)/);
  assert.doesNotMatch(invitation, /returnTo:[^\n]*token|\/invitations\/\$\{/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /:-webkit-autofill/);
  assert.match(styles, /@media \(max-width: 390px\)/);
  assert.match(styles, /@media \(max-width: 340px\)/);
  assert.match(styles, /\.auth-shell \.skin-switcher\s*\{[\s\S]*?min-height:\s*var\(--control-md\)/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*?\.auth-shell \.skin-switcher,[\s\S]*?min-height:\s*var\(--control-lg\)/);
});

test("account center delegates credentials to the IdP and requires versioned typed deactivation", async () => {
  const account = await source("src/AccountCenter.jsx");

  assert.match(account, /密码、MFA、通行密钥和恢复方式全部由正式身份提供方管理/);
  assert.doesNotMatch(account, /type="password"/);
  assert.match(account, /deactivateConfirmation\.trim\(\) === "DEACTIVATE"/);
  assert.match(account, /expectedAuthVersion:\s*account\.auth_version/);
  assert.match(account, /expectedUpdatedAt:\s*account\.updated_at/);
  assert.match(account, /Number\.isInteger\(account\.auth_version\)/);
  assert.match(account, /status === 409/);
  assert.match(account, /完成交接/);
});

test("transient session errors preserve local work while explicit invalidation syncs tabs", async () => {
  const [gateway, sync] = await Promise.all([
    source("src/auth/AuthGateway.jsx"),
    source("src/auth/sessionSync.js"),
  ]);

  assert.match(gateway, /const explicitlyUnauthenticated/);
  assert.match(gateway, /setStatus\(\(current\) => current === "authenticated" \? current : "error"\)/);
  assert.match(gateway, /createAuthSessionSync/);
  assert.match(gateway, /refreshSession\(\{ broadcast: false \}\)/);
  assert.match(gateway, /prompt: "select_account"/);
  assert.match(gateway, /logout_uncertain/);
  assert.match(gateway, /重试并确认服务端退出/);
  assert.match(gateway, /nextError instanceof PlatformApiError && nextError\.status === 401/);
  assert.match(gateway, /preserveInvitation: intent\?\.kind === "switch_invitation_account"/);
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
  assert.match(gateway, /deactivated=\{query\.get\("deactivated"\) === "1"\}/);
  assert.match(callback, /if \(status === "loading"\)/);
  assert.match(callback, /title="正在确认安全会话"[\s\S]*?tone="loading"[\s\S]*?busy/);
  assert.match(login, /deactivated = false/);
  assert.match(login, /账号已停用/);
  assert.match(login, /全部设备会话已经撤销/);
  assert.match(login, /error \|\| deactivated \? "warning" : "secure"/);
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
  assert.doesNotMatch(page, /\{token\}|token=|邀请令牌[^。]*\$\{/);
});

test("protected management creates invitations, transfers ownership and manages global account state", async () => {
  const [management, client, adminContainer] = await Promise.all([
    source("src/ManagementConsole.jsx"),
    platformApiSource(),
    source("src/admin/AdminOperationsContainer.jsx"),
  ]);

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
