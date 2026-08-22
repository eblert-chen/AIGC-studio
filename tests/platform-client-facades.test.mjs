import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import * as publicModule from "../src/api/platformClient.js";
import { createPlatformClient } from "../src/api/platformClient.js";

const EXPECTED_EXPORTS = [
  "PlatformApiError",
  "clearPlatformCsrfToken",
  "createPlatformClient",
  "getPlatformCsrfToken",
  "parseArtifactDownloadUrl",
  "platformClient",
  "readRuntimePlatformConfig",
  "setPlatformCsrfToken",
];

const EXPECTED_CLIENT_KEYS = [
  "isConfigured",
  "isSessionConfigured",
  "companyId",
  "getSessionSurfaces",
  "getPersonalMe",
  "getPersonalWallet",
  "listPersonalModels",
  "listPersonalTasks",
  "getPersonalTask",
  "getPersonalArtifactPreview",
  "getPersonalArtifactDownload",
  "listPersonalArtworks",
  "createPersonalTask",
  "getCompanyMe",
  "listMembers",
  "listPermissionCatalog",
  "getMemberPermissions",
  "createMember",
  "listInvitations",
  "createInvitation",
  "reissueInvitation",
  "revokeInvitation",
  "transferCompanyOwner",
  "setMemberStatus",
  "replaceMemberRoles",
  "replaceMemberAccess",
  "replaceMemberPermissionOverrides",
  "listRoles",
  "createRole",
  "updateRole",
  "deleteRole",
  "listWallet",
  "listLedger",
  "listRecharges",
  "listModelGrants",
  "listResources",
  "listPublisherConnections",
  "listPublisherOAuthProviders",
  "startPublisherOAuth",
  "createPublisherConnection",
  "deletePublisherConnection",
  "listPublicationJobs",
  "createPublicationJob",
  "getPublicationJob",
  "approvePublicationJob",
  "cancelPublicationJob",
  "retryPublicationJob",
  "reconcilePublicationJob",
  "listDownloadRecords",
  "getTaskReport",
  "getConsumptionReport",
  "exportTaskReport",
  "exportConsumptionReport",
  "getPlatformAdminMe",
  "listPlatformUsers",
  "setPlatformUserStatus",
  "getPlatformDashboard",
  "listAdminCompanies",
  "createAdminCompany",
  "reissueAdminCompanyOwnerInvitation",
  "setAdminCompanyStatus",
  "rechargeAdminCompany",
  "listAdminCompanyRecharges",
  "getAdminCompanyEntitlements",
  "listAdminModels",
  "listAdminRelayModels",
  "approveAdminRelayCapability",
  "createAdminModel",
  "updateAdminModel",
  "publishAdminModel",
  "disableAdminModel",
  "deleteAdminModel",
  "listAdminResources",
  "createAdminResource",
  "updateAdminResource",
  "upsertAdminModelGrant",
  "upsertAdminResourceGrant",
  "listAdminAuditLogs",
  "getAdminConsumptionReport",
  "exportAdminConsumptionReport",
  "listAdminChannelCosts",
  "createAdminChannelCost",
  "getAdminOperatingSeries",
  "getAdminTaskOperations",
  "getAdminModelProfitability",
  "getAdminCompanyHealth",
  "getAdminChannelHealth",
  "listAdminRelayChannels",
  "openAdminRelayNativeConsole",
  "getAdminRelayChannel",
  "getAdminRelayChannelOperation",
  "testAdminRelayChannel",
  "setAdminRelayChannelStatus",
  "listAdminRelayUnknownSubmissions",
  "getAdminRelayUnknownSubmission",
  "getAdminRelayUnknownSubmissionResult",
  "resolveAdminRelayUnknownSubmission",
  "listAdminRelayCallbackDeadLetters",
  "getAdminRelayCallbackDeadLetter",
  "getAdminRelayCallbackRedriveResult",
  "redriveAdminRelayCallbackDeadLetter",
  "getAdminDataReadiness",
  "getAdminExceptionCenter",
  "reconcileAdminDownloadGatewayAttempt",
  "getAdminEntitlementMatrix",
  "getAdminEntitlementCoverage",
  "listPlatformAdminPermissionCatalog",
  "listPlatformAdminRoles",
  "createPlatformAdminRole",
  "replacePlatformAdminRole",
  "listPlatformAdministrators",
  "getPlatformAdministratorAccess",
  "replacePlatformAdministratorAccess",
  "setPlatformAdministratorStatus",
  "previewAdminEntitlementBatch",
  "executeAdminEntitlementBatch",
  "previewAdminEntitlementCopy",
  "executeAdminEntitlementCopy",
  "previewAdminEntitlementTemplate",
  "executeAdminEntitlementTemplate",
  "getHomeShowcase",
  "getAdminShowcase",
  "uploadAdminShowcaseMedia",
  "createAdminShowcaseItem",
  "updateAdminShowcaseItem",
  "reorderAdminShowcaseItems",
  "retireAdminShowcaseItem",
  "publishAdminShowcase",
  "unpublishAdminShowcase",
  "rollbackAdminShowcaseRelease",
  "listModels",
  "listAssets",
  "uploadAsset",
  "getAssetPreview",
  "getAssetDownload",
  "deleteAsset",
  "listTaskHistory",
  "listArtworks",
  "listTasks",
  "getTask",
  "getArtifactPreview",
  "getArtifactDownload",
  "promoteArtifactToInputAsset",
  "createTask",
  "cancelTask",
];

test("domain split preserves the complete public export and client method surface", () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-contract",
    fetcher: async () => {
      throw new Error("contract probe must not issue a request");
    },
  });

  assert.deepEqual(Object.keys(publicModule).sort(), EXPECTED_EXPORTS);
  assert.deepEqual(Object.keys(client), EXPECTED_CLIENT_KEYS);
  assert.equal(new Set(EXPECTED_CLIENT_KEYS).size, EXPECTED_CLIENT_KEYS.length);
});

test("domain facades share one transport core and do not become an auth authority", async () => {
  const facadeFiles = [
    "sessionPersonalApi.js",
    "companyApi.js",
    "publishingApi.js",
    "platformAdminApi.js",
    "showcaseApi.js",
    "assetsTasksApi.js",
  ];
  const [entry, core, ...facades] = await Promise.all([
    readFile(new URL("../src/api/platformClient.js", import.meta.url), "utf8"),
    readFile(new URL("../src/api/platformCore.js", import.meta.url), "utf8"),
    ...facadeFiles.map((file) =>
      readFile(new URL(`../src/api/${file}`, import.meta.url), "utf8")
    ),
  ]);

  assert.match(entry, /createPlatformCore\(options\)/);
  for (const file of facadeFiles) {
    assert.match(entry, new RegExp(`from ["']\\./${file}["']`));
  }
  assert.match(core, /credentials:\s*normalizedAccessToken \? "omit" : "include"/);
  assert.match(core, /redirect:\s*"error"/);
  assert.match(core, /X-CSRF-Token/);
  assert.match(core, /AbortController/);
  assert.match(core, /REQUEST_TIMEOUT/);
  assert.match(core, /parseArtifactDownloadUrl/);

  const apiSources = [entry, core, ...facades].join("\n");
  assert.doesNotMatch(
    apiSources,
    /(?:import|from)\s+[^;\n]*(?:authClient|AuthGateway)/,
  );
});
