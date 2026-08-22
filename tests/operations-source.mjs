import { readFileSync } from "node:fs";

const OPERATIONS_SOURCE_FILES = [
  "../src/admin/OperationsConsole.jsx",
  "../src/admin/operations/operationsShared.jsx",
  "../src/admin/operations/TaskOperationsViews.jsx",
  "../src/admin/operations/BusinessEntitlementViews.jsx",
  "../src/admin/operations/ChannelAuditViews.jsx",
  "../src/admin/operations/OperationsDrawers.jsx",
];

export const operationsSource = OPERATIONS_SOURCE_FILES
  .map((path) => readFileSync(new URL(path, import.meta.url), "utf8"))
  .join("\n");
