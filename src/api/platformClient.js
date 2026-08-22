import { createAssetsTasksApi } from "./assetsTasksApi.js";
import { createCompanyApi, createCompanyReportingApi } from "./companyApi.js";
import { createPlatformAdminApi } from "./platformAdminApi.js";
import { createPlatformCore } from "./platformCore.js";
import { createPublishingApi } from "./publishingApi.js";
import { createSessionPersonalApi } from "./sessionPersonalApi.js";
import { createShowcaseApi } from "./showcaseApi.js";

export {
  clearPlatformCsrfToken,
  getPlatformCsrfToken,
  parseArtifactDownloadUrl,
  PlatformApiError,
  readRuntimePlatformConfig,
  setPlatformCsrfToken,
} from "./platformCore.js";

/**
 * Stable public facade for the customer Platform API.
 *
 * Authentication remains owned by authClient/AuthGateway. These domain
 * factories only organize the existing request methods around one shared
 * transport, runtime, error, CSRF, timeout, abort, and credential contract.
 */
export function createPlatformClient(options = {}) {
  const core = createPlatformCore(options);

  return {
    isConfigured: core.isConfigured,
    isSessionConfigured: core.isSessionConfigured,
    companyId: core.companyId,
    ...createSessionPersonalApi(core),
    ...createCompanyApi(core),
    ...createPublishingApi(core),
    ...createCompanyReportingApi(core),
    ...createPlatformAdminApi(core),
    ...createShowcaseApi(core),
    ...createAssetsTasksApi(core),
  };
}

export const platformClient = createPlatformClient();
