export const MANAGEMENT_PAGE_SIZE = 50;

export function emptySnapshot() {
  return {
    me: null,
    members: [],
    invitations: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    roles: [],
    permissions: [],
    wallet: null,
    ledger: null,
    recharges: null,
    models: [],
    grants: [],
    resources: [],
    taskReport: null,
    succeededTaskReport: null,
    consumption: null,
    downloads: null,
    adminMe: null,
    adminUsers: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    companies: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    dashboard: null,
    adminModels: [],
    relayModelAudit: {
      catalog_revision: "",
      items: [],
      platform_only_model_ids: [],
      error: "",
    },
    relayModelReconcile: {
      status: "idle",
      error: "",
      error_status: 0,
    },
    personalModelGrants: { items: [], error: "" },
    adminResources: [],
    channelCosts: null,
    adminConsumption: null,
    audit: { page: 1, page_size: 100, total: 0, items: [] },
  };
}
