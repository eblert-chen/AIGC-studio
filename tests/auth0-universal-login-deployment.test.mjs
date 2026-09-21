import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";

const read = (relativePath) => readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
const manifest = JSON.parse(read("deploy/auth0/universal-login/manifest.json"));
const deployScript = read("scripts/auth0-universal-login.mjs");
const deployReadme = read("deploy/auth0/universal-login/README.md");
const preview = read("docs/auth0-universal-login-preview.html");
const packageJson = JSON.parse(read("package.json"));

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const [red, green, blue] = channels.map((channel) => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
}

function contrastRatio(first, second) {
  const luminances = [relativeLuminance(first), relativeLuminance(second)].sort((a, b) => b - a);
  return (luminances[0] + 0.05) / (luminances[1] + 0.05);
}

function jpegDimensions(relativePath) {
  const source = readFileSync(new URL(`../${relativePath}`, import.meta.url));
  assert.equal(source[0], 0xff);
  assert.equal(source[1], 0xd8);
  let offset = 2;
  while (offset + 9 < source.length) {
    if (source[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = source[offset + 1];
    if (marker === 0xd9 || marker === 0xda) break;
    if (marker === 0x00 || marker === 0xff) {
      offset += 1;
      continue;
    }
    const length = source.readUInt16BE(offset + 2);
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      return {
        height: source.readUInt16BE(offset + 5),
        width: source.readUInt16BE(offset + 7),
      };
    }
    offset += 2 + length;
  }
  throw new Error("JPEG size marker not found");
}

test("Auth0 login manifest preserves the approved brand and split-theme contract", () => {
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.product_name, "旭天 AI studio");
  assert.equal(manifest.client.name, "旭天 AI studio");
  assert.equal(manifest.theme.page_background.page_layout, "right");
  assert.equal(manifest.theme.page_background.background_image_url, manifest.assets.background_url);
  assert.equal(manifest.theme.widget.logo_url, manifest.assets.logo_url);
  assert.equal(manifest.theme.widget.logo_position, "left");
  assert.equal(manifest.theme.widget.header_text_alignment, "left");
  assert.equal(manifest.theme.borders.show_widget_shadow, false);
  assert.equal(manifest.theme.borders.widget_border_weight, 0);
  assert.ok(manifest.theme.borders.input_border_radius >= 8);
  assert.ok(manifest.theme.borders.button_border_radius >= 8);
  assert.equal(manifest.theme.colors.primary_button, "#087B80");
  assert.equal(manifest.theme.colors.base_focus_color, "#087B80");
  assert.equal(manifest.theme.colors.captcha_widget_theme, "light");
  assert.equal(manifest.theme.colors.input_border, "#7A8B88");
  assert.equal(manifest.theme.colors.input_labels_placeholders, "#526469");
  assert.ok(contrastRatio(manifest.theme.colors.input_labels_placeholders, manifest.theme.colors.input_background) >= 4.5);
  assert.ok(contrastRatio(manifest.theme.colors.input_border, manifest.theme.colors.widget_background) >= 3);
  assert.ok(contrastRatio(manifest.theme.colors.base_focus_color, manifest.theme.colors.widget_background) >= 3);
  assert.equal(manifest.theme.fonts.font_url, undefined);
  assert.deepEqual(manifest.tenant.preferred_locales.slice(0, 2), ["zh-CN", "en"]);
  for (const scope of [
    "read:branding",
    "update:branding",
    "delete:branding",
    "read:prompts",
    "update:prompts",
    "read:tenant_settings",
    "update:tenant_settings",
    "read:clients",
    "update:clients",
  ]) assert.ok(manifest.required_scopes.includes(scope), `missing ${scope}`);
  assert.doesNotMatch(JSON.stringify(manifest), /旭天 AI VIDEO|\bWelcome\b|Log in to/i);
  assert.doesNotMatch(JSON.stringify(manifest), /auth0-logo|shield|client_secret|management_token/i);
});

test("Auth0 story background is a versioned, lightweight, prompt-traceable delivery asset", () => {
  const relativePath = manifest.assets.background_path;
  assert.equal(relativePath, "public/auth/xutian-temporal-storyboard-engine-v1.jpg");
  assert.deepEqual(jpegDimensions(relativePath), { width: 2560, height: 1440 });
  assert.ok(statSync(new URL(`../${relativePath}`, import.meta.url)).size < 1_000_000);
  const source = readFileSync(new URL(`../${relativePath}`, import.meta.url));
  assert.ok(source.includes(Buffer.from("impeccable:prompt\0", "utf8")));
  assert.equal(manifest.assets.logo_path, "public/brand/xutian-ai-studio-wordmark.svg");
  assert.equal(manifest.assets.favicon_path, "public/brand/xutian-ai-studio-touch-icon.png");
});

test("Auth0 Chinese copy covers combined and identifier-first login screens", () => {
  const screens = manifest.custom_text.login["zh-CN"];
  assert.deepEqual(Object.keys(screens).sort(), ["login", "login-id", "login-password"]);
  assert.equal(screens.login.title, "登录旭天 AI studio");
  assert.equal(screens.login.description, "继续进入你的创作工作区。身份验证由 Auth0 安全处理，旭天不会保存这些凭据。");
  assert.equal(screens.login.buttonText, "继续");
  assert.equal(screens.login.forgotPasswordText, "忘记密码？");
  assert.equal(screens.login.signupActionLinkText, "创建账号");
  assert.equal(screens.login.showPasswordText, "显示密码");
  assert.equal(screens.login.hidePasswordText, "隐藏密码");
  assert.match(screens.login["wrong-email-credentials"], /不正确/);
});

test("Auth0 deploy command is dry-run by default and fail-closed on live writes", () => {
  assert.equal(packageJson.scripts["auth0:login"], "node scripts/auth0-universal-login.mjs");
  const cleanEnvironment = { ...process.env };
  for (const key of [
    "AUTH0_DOMAIN",
    "AUTH0_MANAGEMENT_TOKEN",
    "AUTH0_MANAGEMENT_API_TOKEN",
    "AUTH0_CLIENT_ID",
    "AUTH0_ASSET_BASE_URL",
  ]) delete cleanEnvironment[key];
  const plan = spawnSync(process.execPath, ["scripts/auth0-universal-login.mjs"], {
    cwd: new URL("../", import.meta.url),
    encoding: "utf8",
    env: cleanEnvironment,
  });
  assert.equal(plan.status, 0, plan.stderr);
  const result = JSON.parse(plan.stdout);
  assert.equal(result.mode, "dry-run");
  assert.equal(result.live_write, false);
  assert.match(result.apply_command, /--apply/);

  const missingToken = spawnSync(process.execPath, ["scripts/auth0-universal-login.mjs", "--apply"], {
    cwd: new URL("../", import.meta.url),
    encoding: "utf8",
    env: {
      ...cleanEnvironment,
      AUTH0_DOMAIN: "dev-example.us.auth0.com",
      AUTH0_CLIENT_ID: "client-example",
      AUTH0_ASSET_BASE_URL: "https://assets.example.invalid",
    },
  });
  assert.notEqual(missingToken.status, 0);
  assert.match(missingToken.stderr, /AUTH0_MANAGEMENT_TOKEN/);

  const insecureAssets = spawnSync(process.execPath, ["scripts/auth0-universal-login.mjs", "--apply"], {
    cwd: new URL("../", import.meta.url),
    encoding: "utf8",
    env: {
      ...cleanEnvironment,
      AUTH0_DOMAIN: "dev-example.us.auth0.com",
      AUTH0_CLIENT_ID: "client-example",
      AUTH0_MANAGEMENT_TOKEN: "never-print-this-token",
      AUTH0_ASSET_BASE_URL: "http://assets.example.invalid",
    },
  });
  assert.notEqual(insecureAssets.status, 0);
  assert.match(insecureAssets.stderr, /必须使用 HTTPS/);
  assert.doesNotMatch(`${insecureAssets.stdout}${insecureAssets.stderr}`, /never-print-this-token/);
});

test("Auth0 deploy tool probes New Universal Login and protects replace-all APIs", () => {
  assert.match(deployScript, /api\("\/prompts"\)/);
  assert.match(deployScript, /universal_login_experience !== "new"/);
  assert.match(deployScript, /api\("\/branding\/themes\/default"/);
  assert.match(deployScript, /themePayload\(theme, manifest\.theme\)/);
  assert.match(deployScript, /deepMerge\([\s\S]*?promptText \|\| \{\}[\s\S]*?manifest\.custom_text\.login\["zh-CN"\]/);
  assert.match(deployScript, /assertUnchanged/);
  assert.match(deployScript, /assertApplied/);
  assert.match(deployScript, /await rollback\(backupPath\)/);
  assert.match(deployScript, /\.local-backups/);
  assert.doesNotMatch(deployScript, /console\.log\([^\n]*(?:token|Authorization)/i);
  assert.match(deployReadme, /默认 dry-run/);
  assert.match(deployReadme, /不会提交邮箱或密码|表单不会发送凭据/);
  assert.match(deployReadme, /Page Template.*不要求|不使用 Page Template/);
});

test("local Auth0 preview matches the selected layout without impersonating real auth", () => {
  assert.match(preview, /grid-template-columns:\s*minmax\(0, 57fr\) minmax\(460px, 43fr\)/);
  assert.match(preview, /xutian-temporal-storyboard-engine-v1\.jpg/);
  assert.match(preview, /xutian-ai-studio-wordmark\.svg/);
  assert.match(preview, /登录旭天 AI studio/);
  assert.match(preview, /继续进入你的创作工作区。身份验证由 Auth0 安全处理，旭天不会保存这些凭据。/);
  assert.match(preview, /@media \(max-height: 820px\) and \(min-width: 761px\)/);
  assert.doesNotMatch(preview, /password\.focus\(\)/);
  assert.equal((preview.match(/:hover \{ border-color: var\(--field-border\)/g) || []).length, 2);
  assert.match(preview, /event\.preventDefault\(\)/);
  assert.match(preview, /不会提交邮箱或密码/);
  assert.doesNotMatch(preview, /<form[^>]+action=/i);
  assert.doesNotMatch(preview, /fetch\(|XMLHttpRequest|location\s*=/);
  assert.match(preview, /@media \(max-width: 390px\)/);
  assert.match(preview, /@media \(max-width: 340px\)/);
  assert.match(preview, /min-height:\s*54px/);
  assert.match(preview, /:focus-visible/);
  assert.equal((preview.match(/<h1\b/g) || []).length, 1);
});
