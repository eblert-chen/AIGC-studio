import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  collectionItems,
  instantToLocalSchedule,
  localScheduleToOffsetIso,
  publicationActionAvailability,
  publicationJobArtifactId,
} from "../src/publishing.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const centerSource = await readFile(new URL("../src/PublishingCenter.jsx", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("../src/publishing.css", import.meta.url), "utf8");

test("publishing history navigation requires read permission but not the active entitlement", () => {
  assert.match(appSource, /permissionCodes\.includes\("publish\.accounts\.read"\)/);
  assert.match(appSource, /permissionCodes\.includes\("publish\.jobs\.read"\)/);
  assert.match(appSource, /resource\.key \|\| resource\.resource_key/);
  assert.match(appSource, /=== "feature\.auto_publish"/);
  assert.match(appSource, /showPublishingNavigation = isPersonalWorkspace \|\| \([\s\S]*?hasCompanySession && \(DEMO_MODE \|\| hasPublishingPermission\)[\s\S]*?\)/);
  assert.match(appSource, /if \(isPersonalWorkspace\) \{[\s\S]*?capability="发布"/);
  assert.match(appSource, /autoPublishingEnabled=\{hasAutoPublishEntitlement\}/);
  assert.match(appSource, /item\.id !== "publish" \|\| showPublishingNavigation/);
  assert.match(appSource, /activeNav !== "publish" \|\| showPublishingNavigation/);
  assert.doesNotMatch(appSource, /showPublishingNavigation\s*=\s*hasCompanySession[^;]+hasAutoPublishEntitlement/);
});

test("disabled auto publishing preserves history and safety actions only", () => {
  assert.match(centerSource, /公司已停用自动发布，仅保留历史与安全处置/);
  assert.match(centerSource, /externalPublishingEnabled && canManageJobs && available\.approve/);
  assert.match(centerSource, /externalPublishingEnabled && canManageJobs && available\.retry/);
  assert.match(centerSource, /externalPublishingEnabled && canManageAccounts/);
  assert.match(centerSource, /canManageJobs && job\.status === "submission_unknown"/);
  assert.match(centerSource, /canManageJobs && available\.cancel/);
  assert.doesNotMatch(centerSource, /externalPublishingEnabled && canManageJobs && job\.status === "submission_unknown"/);
  assert.doesNotMatch(centerSource, /externalPublishingEnabled && canManageJobs && available\.cancel/);
});

test("publishing center uses Platform APIs and never fabricates demo accounts or jobs", () => {
  assert.match(centerSource, /client\.listPublisherConnections/);
  assert.match(centerSource, /client\.listPublicationJobs/);
  assert.match(centerSource, /client\.createPublicationJob/);
  assert.match(centerSource, /client\.approvePublicationJob/);
  assert.match(centerSource, /client\.cancelPublicationJob/);
  assert.match(centerSource, /client\.retryPublicationJob/);
  assert.doesNotMatch(centerSource, /DEMO_(?:PUBLICATION|PUBLISHER|CONNECTION)/);
  assert.match(centerSource, /测试发布没有伪造任务/);
  assert.match(centerSource, /测试发布没有真实账号/);
  assert.match(centerSource, /不会连接真实社交平台/);
});

test("production publisher linking uses the server-owned OAuth contract", () => {
  assert.match(centerSource, /client\.listPublisherOAuthProviders/);
  assert.match(centerSource, /client\.startPublisherOAuth\(\{ provider: providerKey \}\)/);
  assert.match(centerSource, /window\.location\.assign\(authorizationUrl\.href\)/);
  assert.match(centerSource, /访问令牌只保存在适配器的加密密钥存储中/);
  assert.match(centerSource, /服务端尚未配置支持 OAuth 的正式发布平台/);
  assert.doesNotMatch(centerSource, /access_token|refresh_token/);
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
    /if \(!externalPublishingEnabled \|\| !canManageJobs \|\| !canReadJobs \|\| !canReadAccounts\)/,
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
  assert.match(centerSource, /未指定要发布的归档作品，请从作品页重新发起发布/);
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
  assert.match(centerSource, /人工核销/);
  assert.doesNotMatch(centerSource, /job\.status === "submission_unknown"[\s\S]{0,180}mutateJob\(job, "retry"\)/);
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
  assert.match(centerSource, /Mock 测试/);
});

test("mock publisher creation is compile-time gated to development", () => {
  assert.match(centerSource, /const DEVELOPMENT_MOCK_PUBLISHING = import\.meta\.env\.DEV && !import\.meta\.env\.PROD/);
  assert.match(centerSource, /if \(!DEVELOPMENT_MOCK_PUBLISHING\)/);
  assert.match(centerSource, /DEVELOPMENT_MOCK_PUBLISHING && externalPublishingEnabled && canManageAccounts/);
  assert.match(centerSource, /DEVELOPMENT_MOCK_PUBLISHING && externalPublishingEnabled && testConnectionOpen/);
  assert.match(centerSource, /生产环境禁止创建 Mock 发布连接/);
});

test("publishing layout remains a light secondary workspace and collapses on mobile", () => {
  assert.match(stylesSource, /\.publication-layout\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) minmax\(300px, 360px\)/);
  assert.match(stylesSource, /\.publication-heading h1\s*\{[\s\S]*?font-size:\s*28px/);
  assert.match(stylesSource, /\.publication-jobs,\s*[\s\S]*?\.publication-connections\s*\{[\s\S]*?border-radius:\s*0;[\s\S]*?background:\s*transparent/);
  assert.match(stylesSource, /\.publication-center button,[\s\S]*?min-height:\s*38px;[\s\S]*?border-radius:\s*3px/);
  const mobile = stylesSource.slice(stylesSource.indexOf("@media (max-width: 900px)"));
  assert.match(mobile, /\.publication-layout\s*\{\s*grid-template-columns:\s*1fr/);
  assert.doesNotMatch(stylesSource, /#[0-9a-f]{3,8}[^\n]*(?:linear|radial)-gradient/i);
  assert.doesNotMatch(stylesSource, /#[0-9a-f]{3,8}/i);
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
