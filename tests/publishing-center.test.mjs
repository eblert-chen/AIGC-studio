import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  collectionItems,
  instantToLocalSchedule,
  localScheduleToOffsetIso,
  publicationActionAvailability,
  publicationJobArtifactId,
  publicationStatus,
  PUBLICATION_STATUS_FILTER_GROUPS,
} from "../src/publishing.js";
import {
  authenticationMethodLabel,
  findVerifiedOAuthConnection,
  oauthFailureMessage,
  publishingBlockerLabel,
  publishingBlockerSummary,
  publishingConnectionStatusLabel,
  publishingPermissionGuidance,
  publishingProviderLabel,
  safePublishingMessage,
} from "../src/publishingPresentation.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const centerSource = await readFile(new URL("../src/PublishingCenter.jsx", import.meta.url), "utf8");
const stylesSource = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const mobileStylesSource = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);

test("publishing history navigation requires read permission but not the active entitlement", () => {
  assert.match(appSource, /permissionCodes\.includes\("publish\.accounts\.read"\)/);
  assert.match(appSource, /permissionCodes\.includes\("publish\.jobs\.read"\)/);
  assert.match(appSource, /companyClient[\s\S]*?\.getPublishingReadiness\(\{ signal: controller\.signal \}\)/);
  assert.match(appSource, /publishingReadiness\?\.feature_auto_publish_enabled === true/);
  assert.match(appSource, /publishingReadiness\?\.account_side_effects_enabled === true/);
  assert.match(appSource, /publishingReadiness\?\.job_side_effects_enabled === true/);
  assert.doesNotMatch(appSource, /\.listResources\(\{ signal: controller\.signal \}\)[\s\S]{0,400}feature\.auto_publish/);
  assert.match(appSource, /showPublishingNavigation = isPersonalWorkspace \|\| \([\s\S]*?hasCompanySession && \(DEMO_MODE \|\| hasPublishingPermission\)[\s\S]*?\)/);
  assert.match(appSource, /if \(isPersonalWorkspace\) \{[\s\S]*?capability="发布"/);
  assert.match(appSource, /autoPublishingEnabled=\{hasAutoPublishEntitlement\}/);
  assert.match(appSource, /item\.id === "publish"[\s\S]*?\? showPublishingNavigation[\s\S]*?: studioRouteAvailable/);
  assert.match(appSource, /activeNav !== "publish" \|\| showPublishingNavigation/);
  assert.doesNotMatch(appSource, /showPublishingNavigation\s*=\s*hasCompanySession[^;]+hasAutoPublishEntitlement/);
});

test("disabled auto publishing preserves history and safety actions only", () => {
  assert.match(centerSource, /公司已停用自动发布/);
  assert.match(centerSource, /你仍可查看历史；有管理权限时可取消尚未提交的任务，或核对结果未知的任务/);
  assert.match(centerSource, /jobPublishingEnabled && available\.approve/);
  assert.match(centerSource, /jobPublishingEnabled && available\.retry/);
  assert.match(centerSource, /accountPublishingEnabled/);
  assert.match(centerSource, /canManageJobs && job\.status === "submission_unknown"/);
  assert.match(centerSource, /canManageJobs && available\.cancel/);
  assert.doesNotMatch(centerSource, /jobPublishingEnabled && canManageJobs && job\.status === "submission_unknown"/);
  assert.doesNotMatch(centerSource, /jobPublishingEnabled && canManageJobs && available\.cancel/);
  assert.match(centerSource, /暂时无法确认发布条件/);
  assert.match(centerSource, /新建与连接操作已关闭，现有记录仍可查看和安全处置/);
});

test("publishing center uses Platform APIs, keeps demo data truthful, and exposes no development mock creator", () => {
  assert.match(centerSource, /client\.listPublisherConnections/);
  assert.match(centerSource, /client\.listPublicationJobs/);
  assert.match(centerSource, /client\.createPublicationJob/);
  assert.match(centerSource, /client\.approvePublicationJob/);
  assert.match(centerSource, /client\.cancelPublicationJob/);
  assert.match(centerSource, /client\.retryPublicationJob/);
  assert.doesNotMatch(centerSource, /DEMO_(?:PUBLICATION|PUBLISHER|CONNECTION)/);
  assert.match(centerSource, /演示模式暂无发布任务/);
  assert.match(centerSource, /演示模式未连接发布账号/);
  assert.match(centerSource, /不会连接真实社交平台/);
  assert.match(centerSource, /预览不会提交/);
  assert.doesNotMatch(centerSource, /测试发布不提交/);
  assert.match(centerSource, /!demoMode && \([\s\S]*?type="submit"[\s\S]*?提交待审核/);
  assert.doesNotMatch(
    centerSource,
    /DEVELOPMENT_MOCK_PUBLISHING|createTestConnection|testConnectionOpen|testConnectionName|createPublisherConnection|添加开发 Mock 连接|创建 Mock 连接/,
  );
  assert.match(centerSource, /const isMockJob = [^;]+[\s\S]*?测试账号/);
});

test("production publisher linking uses the server-owned OAuth contract", () => {
  assert.match(centerSource, /client\.listPublisherOAuthProviders/);
  assert.match(centerSource, /client\.startPublisherOAuth\(\{ provider: providerKey \}\)/);
  assert.match(centerSource, /window\.location\.assign\(authorizationUrl\.href\)/);
  assert.match(centerSource, /账号连接由平台安全处理，登录信息不会显示在这里/);
  assert.match(centerSource, /当前没有可连接的发布平台，请联系公司管理员/);
  assert.doesNotMatch(centerSource, /access_token|refresh_token/);
});

test("publishing heading and guidance use customer language without implementation jargon", () => {
  assert.match(centerSource, /<h1 id="publication-title">发布<\/h1>/);
  assert.match(centerSource, /选择已保存的作品，安排发布时间并提交审核；审核通过后才会发布到已连接的账号/);
  assert.match(centerSource, /演示模式[\s\S]*?发布暂不可用[\s\S]*?仅查看历史[\s\S]*?可以新建发布[\s\S]*?正在检查发布条件/);
  assert.match(centerSource, /使用已保存作品/);
  assert.doesNotMatch(
    centerSource,
    /从长期归档作品创建可审计的发布交接|制品身份|canonical artifact_id|适配器的加密密钥存储/,
  );
});

test("OAuth return hints trigger server verification before any connected success", () => {
  const effectStart = centerSource.indexOf('const result = url.searchParams.get("publishing_oauth")');
  const effectEnd = centerSource.indexOf("useEffect(() => {", effectStart + 1);
  const oauthEffect = centerSource.slice(effectStart, effectEnd);
  assert.ok(effectStart >= 0 && effectEnd > effectStart);
  assert.match(oauthEffect, /state: "confirming"/);
  assert.match(oauthEffect, /refreshConnections\(\{ signal: controller\.signal \}\)/);
  assert.match(oauthEffect, /findVerifiedOAuthConnection\(nextConnections, provider\)/);
  assert.match(oauthEffect, /账号已由服务端确认并可用于发布/);
  assert.doesNotMatch(oauthEffect, /发布账号已安全连接/);

  const active = { id: "connection-1", provider: "douyin", status: "active" };
  assert.equal(findVerifiedOAuthConnection([active], "douyin"), active);
  assert.equal(findVerifiedOAuthConnection([{ ...active, status: "disabled" }], "douyin"), null);
  assert.equal(findVerifiedOAuthConnection([active], "tiktok"), null);
  assert.equal(findVerifiedOAuthConnection([active], ""), null);
});

test("publishing presentation maps server codes without leaking raw identifiers", () => {
  const blockers = [
    "feature_not_configured",
    "feature_retired",
    "company_grant_missing",
    "company_grant_disabled",
    "company_grant_not_yet_effective",
    "company_grant_expired",
    "no_publish_manage_permission",
  ];
  for (const blocker of blockers) {
    const label = publishingBlockerLabel(blocker);
    assert.ok(label.length > 0);
    assert.doesNotMatch(label, new RegExp(blocker.replaceAll(".", "\\.")));
  }
  const unknownBlocker = publishingBlockerLabel("provider_future_gate");
  assert.equal(unknownBlocker, "发布条件尚未满足，请联系公司管理员核对授权与权限");
  assert.doesNotMatch(unknownBlocker, /provider_future_gate/);
  assert.doesNotMatch(
    publishingBlockerSummary(["feature_retired", "provider_future_gate"]),
    /feature_retired|provider_future_gate/,
  );
  assert.equal(publishingPermissionGuidance("publish.jobs.read"), "请联系公司管理员为你开启“查看发布任务”权限。");
  assert.equal(publishingPermissionGuidance("future.permission"), "请联系公司管理员为你开启“所需的发布权限”权限。");
  assert.equal(authenticationMethodLabel("mfa"), "多因素验证");
  assert.equal(authenticationMethodLabel("future_amr"), "身份服务验证");
  assert.equal(publishingProviderLabel("douyin"), "抖音");
  assert.equal(publishingProviderLabel("future_provider"), "其他发布平台");
  assert.equal(publishingConnectionStatusLabel("requires_reauth"), "需要重新授权");
  assert.equal(publishingConnectionStatusLabel("future_status"), "状态待确认");
  assert.equal(oauthFailureMessage("provider_denied"), "你已取消授权，发布账号尚未连接。");
  assert.doesNotMatch(oauthFailureMessage("future_reason"), /future_reason/);
  assert.equal(safePublishingMessage("INTERNAL_PROVIDER_FAILURE"), "发布服务未完成请求，请稍后重试。");
  assert.equal(publicationStatus("future_status").label, "状态待确认");
});

test("publishing workflow groups real states around attention schedule and history", () => {
  assert.deepEqual(PUBLICATION_STATUS_FILTER_GROUPS.map(({ label }) => label), ["待处理", "已排期", "历史"]);
  assert.match(centerSource, /PUBLICATION_STATUS_FILTER_GROUPS\.map/);
  assert.match(centerSource, /重新进入发布队列/);
  assert.match(centerSource, /取消待发布任务/);
  assert.match(centerSource, /发布前要求/);
  assert.match(centerSource, /新建发布时会逐项检查/);
  assert.match(centerSource, /使用已保存作品[\s\S]*?人工批准[\s\S]*?核对未知结果/);
  assert.doesNotMatch(centerSource, /交接校验|不可跳过规则|canonical artifact_id/);
});

test("publication jobs submit one archived artifact with an offset-aware schedule", () => {
  assert.match(centerSource, /artifactId,/);
  assert.match(centerSource, /connectionId,/);
  assert.match(centerSource, /idempotencyKey: submissionKeyRef\.current/);
  assert.match(centerSource, /localScheduleToOffsetIso\(scheduledLocal, timezone\)/);
  assert.equal(
    localScheduleToOffsetIso("2026-08-08T10:00", "Asia/Shanghai"),
    "2026-08-08T10:00:00+08:00",
  );
});

test("external publish requests open only after authorization and preselect an exact archived artifact", () => {
  assert.match(centerSource, /initialArtifactId = ""/);
  assert.match(centerSource, /openComposerRequest = null/);
  assert.match(centerSource, /if \(!publishingEntitlementResolved && !demoMode\) return/);
  assert.match(
    centerSource,
    /if \(!jobPublishingEnabled \|\| !canReadJobs \|\| !canReadAccounts\)/,
  );
  assert.match(centerSource, /handledOpenComposerRequestRef\.current === requestKey/);
  assert.match(centerSource, /setComposerSelectionRequest\(requestKey\)/);
  assert.match(centerSource, /setComposerOpen\(true\)/);
  assert.match(
    centerSource,
    /artworks\.find\([\s\S]*?publicationArtifactId\(artwork\) === requestedArtifactId/,
  );
  assert.match(centerSource, /publicationArtifactId\(composerInitialArtwork\) === composerInitialArtifactId/);
  assert.match(centerSource, /\? composerInitialArtwork/);
  assert.match(centerSource, /setArtifactId\(publicationArtifactId\(matchedArtwork\)\)/);
});

test("an invalid external artifact selection fails closed without selecting another work", () => {
  assert.match(centerSource, /setArtifactId\(""\);[\s\S]*?指定作品未在当前可发布作品中找到/);
  assert.match(centerSource, /未指定要发布的作品，请从作品页重新发起发布/);
  assert.match(centerSource, /无法核对指定作品/);
  assert.match(centerSource, /initialSelectionRequest !== null && initialSelectionRequest !== undefined/);
  assert.doesNotMatch(
    centerSource,
    /requestedArtifactId[\s\S]{0,500}setArtifactId\([^)]*publicationArtifactId\(artworks\[0\]\)/,
  );
});

test("manual new-publication keeps the first-work fallback and external open never submits a job", () => {
  assert.match(centerSource, /const openManualComposer = \(\) => \{[\s\S]*?setComposerInitialArtifactId\(""\)[\s\S]*?setComposerSelectionRequest\(null\)[\s\S]*?setComposerOpen\(true\)/);
  assert.match(centerSource, /setArtifactId\(\(current\) => current \|\| publicationArtifactId\(artworks\[0\]\)\)/);

  const externalEffectStart = centerSource.indexOf("if (openComposerRequest === null");
  const externalEffectEnd = centerSource.indexOf("return (", externalEffectStart);
  const externalEffect = centerSource.slice(externalEffectStart, externalEffectEnd);
  assert.ok(externalEffectStart >= 0 && externalEffectEnd > externalEffectStart);
  assert.doesNotMatch(externalEffect, /createJob|createPublicationJob|onSubmit/);
});

test("IANA schedule conversion rejects DST gaps and ambiguous wall times", () => {
  assert.throws(
    () => localScheduleToOffsetIso("2026-03-08T02:30", "America/Los_Angeles"),
    /不存在这个本地时间/,
  );
  assert.throws(
    () => localScheduleToOffsetIso("2026-11-01T01:30", "America/Los_Angeles"),
    /存在两个可能时刻/,
  );
  assert.equal(
    localScheduleToOffsetIso("2026-03-08T03:30", "America/Los_Angeles"),
    "2026-03-08T03:30:00-07:00",
  );
  assert.equal(
    localScheduleToOffsetIso("2026-11-01T02:30", "America/Los_Angeles"),
    "2026-11-01T02:30:00-08:00",
  );
  assert.throws(
    () => localScheduleToOffsetIso("2026-08-08T10:00", "Not/A_Timezone"),
    /时区无效/,
  );
});

test("schedule input defaults and minimums are formatted in the selected IANA zone", () => {
  const instant = new Date("2026-03-08T10:30:00.000Z");
  assert.equal(instantToLocalSchedule(instant, "America/Los_Angeles"), "2026-03-08T03:30");
  assert.equal(instantToLocalSchedule(instant, "Asia/Tokyo"), "2026-03-08T19:30");
  assert.match(centerSource, /minimumScheduledLocal = instantToLocalSchedule/);
  assert.match(centerSource, /changeTimezone\(event\.target\.value\)/);
  assert.match(centerSource, /原发布时间在新时区无效或已过期/);
});

test("unknown submissions cannot be retried while deterministic failures can", () => {
  assert.deepEqual(publicationActionAvailability("submission_unknown"), {
    approve: false,
    cancel: false,
    retry: false,
  });
  assert.equal(publicationActionAvailability("failed").retry, true);
  assert.match(centerSource, /禁止自动重试/);
  assert.match(centerSource, /必须先去渠道后台核对，避免重复发布/);
  assert.match(centerSource, /canManageJobs && job\.status === "submission_unknown"/);
  assert.match(centerSource, /client\.reconcilePublicationJob/);
  assert.match(centerSource, /核对并确认渠道结果/);
  assert.doesNotMatch(centerSource, /人工核销/);
  assert.doesNotMatch(centerSource, /job\.status === "submission_unknown"[\s\S]{0,180}mutateJob\(job, "retry"\)/);
});

test("publisher connections use disable semantics and preserve history", () => {
  assert.match(centerSource, /const disableConnection = async/);
  assert.match(centerSource, /client\.deletePublisherConnection\(connection\.id\)/);
  assert.match(centerSource, /停用连接/);
  assert.match(centerSource, /确认停用/);
  assert.match(centerSource, /历史发布任务和安全处置仍会保留/);
  assert.doesNotMatch(centerSource, /确认移除|发布连接已移除/);
});

test("publication collections accept both arrays and paginated Platform responses", () => {
  const items = [{ id: "job-1" }];
  assert.equal(collectionItems(items), items);
  assert.equal(collectionItems({ items }), items);
  assert.deepEqual(collectionItems(null), []);
});

test("publication responses resolve the persisted TaskArtifact identifier", () => {
  assert.equal(publicationJobArtifactId({ task_artifact_id: "artifact-1" }), "artifact-1");
  assert.equal(publicationJobArtifactId({ artifact_id: "legacy-artifact" }), "legacy-artifact");
  assert.match(centerSource, /external_post_url/);
  assert.match(centerSource, /external_post_id/);
  assert.match(centerSource, /error_message/);
  assert.match(centerSource, /测试账号/);
});

test("customer publishing surface completely removes the development mock creation entry", () => {
  assert.doesNotMatch(centerSource, /DEVELOPMENT_MOCK_PUBLISHING/);
  assert.doesNotMatch(centerSource, /createTestConnection|testConnectionOpen|testConnectionName/);
  assert.doesNotMatch(centerSource, /client\.createPublisherConnection/);
  assert.doesNotMatch(centerSource, /添加开发 Mock 连接|创建 Mock 连接|仅限开发环境/);
  assert.match(centerSource, /isMockJob[\s\S]*?测试账号/);
});

test("publishing layout puts a horizontal requirements strip above rounded account and job work surfaces", () => {
  assert.match(
    stylesSource,
    /\.publication-layout\s*\{[^}]*grid-template-columns:\s*minmax\(280px, 330px\) minmax\(0, 1fr\);[^}]*grid-template-areas:\s*"requirements requirements"\s*"connections jobs";[^}]*align-items:\s*start;[^}]*gap:\s*16px;/s,
  );
  assert.match(stylesSource, /\.publication-handoff-index\s*\{\s*grid-area:\s*requirements;\s*\}/);
  assert.match(stylesSource, /\.publication-jobs\s*\{\s*grid-area:\s*jobs;\s*\}/);
  assert.match(stylesSource, /\.publication-connections\s*\{\s*grid-area:\s*connections;\s*\}/);
  assert.match(
    stylesSource,
    /\.publication-handoff-index \.publication-job-list\s*\{\s*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);\s*\}/,
  );
  assert.match(
    stylesSource,
    /\.publication-handoff-index \.publication-job\s*\{[^}]*min-height:\s*92px;[^}]*align-content:\s*center;/s,
  );
  assert.match(
    stylesSource,
    /\.secondary-heading h1,[\s\S]*?\.publication-heading h1\s*\{[^}]*font-size:\s*clamp\(24px, 2\.4vw, 30px\)/,
  );
  assert.match(
    stylesSource,
    /\.publication-connections,[\s\S]*?\.publication-jobs\s*\{[^}]*border-radius:\s*16px;[^}]*background:\s*var\(--surface\);[^}]*box-shadow:/s,
  );
  assert.match(
    stylesSource,
    /\.publication-artwork-preview-button\s*\{[^}]*width:\s*100%;[^}]*border-radius:\s*0;[^}]*background:\s*transparent;/s,
  );
  assert.match(
    stylesSource,
    /\.publication-primary-button\s*\{[^}]*color:\s*#fff;[^}]*border:\s*0;[^}]*background:\s*var\(--signal\);[^}]*border-radius:\s*10px;/s,
  );
  assert.match(
    stylesSource,
    /@media \(max-width:\s*980px\)[\s\S]*?\.publication-layout\s*\{[^}]*grid-template-columns:\s*1fr;[^}]*grid-template-areas:\s*"requirements"\s*"jobs"\s*"connections";/s,
  );
  assert.match(
    stylesSource,
    /@media \(max-width:\s*760px\)[\s\S]*?\.publication-handoff-index \.publication-job-list\s*\{\s*grid-template-columns:\s*1fr;\s*\}/,
  );
  assert.match(
    mobileStylesSource,
    /\.app-shell\[data-theme\] \.publication-center button,[\s\S]*?min-height:\s*44px;/,
  );
  assert.doesNotMatch(
    stylesSource,
    /[^{}]*\.publication[^{}]*\{[^{}]*(?:linear|radial)-gradient/i,
  );
  assert.doesNotMatch(
    stylesSource,
    /\.publication-layout\s*\{[^}]*grid-template-columns:\s*minmax\(250px, \.8fr\) minmax\(360px, 1\.3fr\) minmax\(300px, 1fr\)/s,
  );
});

test("all publication dialogs trap focus, close on Escape, and restore the trigger", () => {
  assert.match(centerSource, /function useAccessibleDialog\(open, onClose\)/);
  assert.match(centerSource, /event\.key === "Escape"/);
  assert.match(centerSource, /event\.key !== "Tab"/);
  assert.match(centerSource, /returnFocusTarget\.focus\(\{ preventScroll: true \}\)/);
  assert.match(centerSource, /document\.body\.style\.overflow = "hidden"/);
  assert.equal((centerSource.match(/const dialogRef = useAccessibleDialog/g) || []).length, 3);
  assert.equal((centerSource.match(/data-dialog-initial-focus/g) || []).length, 4);
  assert.equal((centerSource.match(/tabIndex=\{-1\}/g) || []).length, 3);
});

test("publication composer pages archived works and loads previews without download issuance", () => {
  assert.match(centerSource, /const PUBLICATION_ARTWORK_PAGE_SIZE = 24/);
  assert.match(centerSource, /client\.listArtworks\([\s\S]*?page: composerArtworksPage/);
  assert.match(centerSource, /composerArtworksTotal/);
  assert.match(centerSource, /previewUrl=\{activePreviewUrl\(previewUrls\[previewKey\]\)\}/);
  assert.match(centerSource, /previewLoading=\{previewActionKey === `preview:\$\{previewKey\}`\}/);
  assert.match(centerSource, /onPreviewError=\{\(\) => onPreviewError\?\.\(previewKey\)\}/);
  assert.match(centerSource, /onRequestArtworkPreview/);
  assert.match(centerSource, /initialArtworkScope === "company"/);
  assert.match(centerSource, /onRequestArtworkPreview\?\.\(artwork, scope\)/);
  assert.match(stylesSource, /\.publication-artwork-preview-button/);
  assert.match(appSource, /onRequestArtworkPreview=\{\(artwork, scope = "mine"\) => accessArtifact\(artwork,/);
  assert.match(appSource, /preview: true/);
  assert.match(appSource, /studioClient\.getArtifactPreview/);
  assert.match(appSource, /\.\.\.\(!isPersonalWorkspace \? \{ scope \} : \{\}\)/);
  assert.match(appSource, /if \(!preview\) \{[\s\S]*?setIssuedArtifacts/);
  assert.match(centerSource, /artwork\.media_type === "video" && previewUrl/);
  assert.match(centerSource, /<video[\s\S]*?muted[\s\S]*?playsInline[\s\S]*?preload="metadata"/);
});
