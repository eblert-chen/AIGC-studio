import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIVE_PERMISSION_CATALOG,
  ACTIVE_PERMISSION_CODES,
  requirePermissionCatalog,
} from "../src/permissionCatalog.js";

test("active permission catalog contains all 16 company permissions", () => {
  assert.equal(ACTIVE_PERMISSION_CATALOG.length, 16);
  assert.equal(ACTIVE_PERMISSION_CODES.length, 16);
  assert.deepEqual(
    ACTIVE_PERMISSION_CODES.filter((code) => code.startsWith("publish.")),
    [
      "publish.accounts.read",
      "publish.accounts.manage",
      "publish.jobs.read",
      "publish.jobs.manage",
    ],
  );
  assert.equal(ACTIVE_PERMISSION_CODES.includes("models.manage"), false);
  assert.equal(ACTIVE_PERMISSION_CODES.includes("tasks.manage"), false);
});

test("live permission editing fails closed when the server catalog is empty", () => {
  assert.throws(
    () => requirePermissionCatalog([]),
    /权限目录为空或尚未加载/,
  );
  assert.throws(
    () => requirePermissionCatalog(null),
    /权限目录为空或尚未加载/,
  );
});

test("permission editing uses the complete server-owned catalog", () => {
  const catalog = [
    { code: "users.read", description: "查看成员" },
    { code: "future.feature.manage", description: "未来新增权限" },
  ];

  assert.equal(requirePermissionCatalog(catalog), catalog);
});
