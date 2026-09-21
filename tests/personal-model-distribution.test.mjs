import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource as management } from "./management-source.mjs";

import {
  buildPersonalModelGrantPayload,
  normalizePersonalModelGrantCatalog,
} from "../src/admin/modelCapabilityReleases.js";
import { createPlatformClient } from "../src/api/platformClient.js";

const component = await readFile(
  new URL("../src/components/management/PlatformModelReleases.jsx", import.meta.url),
  "utf8",
);
const css = await readFile(
  new URL("../src/design-system/management-routes.css", import.meta.url),
  "utf8",
);
const mobileCss = await readFile(
  new URL("../src/design-system/mobile-management.css", import.meta.url),
  "utf8",
);

const publishedModel = {
  id: "model-seedream",
  slug: "seedream-5",
  display_name: "Seedream 5",
  billing_mode: "per_item",
  status: "published",
  capability_version: 3,
};

const grant = {
  model_id: publishedModel.id,
  model_slug: publishedModel.slug,
  model_display_name: publishedModel.display_name,
  model_status: "published",
  capability_version: 3,
  relay_capability_revision: "sha256:relay-capability-v3",
  quote_revision: "quote-v7",
  enabled: false,
  price_per_item_points: 4,
  price_per_second_points: null,
  config_override: {},
  effective_capabilities: {
    schema_version: 1,
    modes: { text_to_image: { limits: { output_counts: [1] } } },
  },
};

const readyRelayEvidence = {
  items: [{
    platform_model_id: publishedModel.id,
    relay_model_id: publishedModel.slug,
    status: "identical",
    route_evidence_status: "ready",
    routing_release_sha256: "f".repeat(64),
  }],
};

test("personal retail catalog joins server grants with model billing and release evidence", () => {
  const catalog = normalizePersonalModelGrantCatalog(
    [grant],
    [publishedModel],
    readyRelayEvidence,
  );
  assert.equal(catalog.items.length, 1);
  assert.equal(catalog.items[0].billingMode, "per_item");
  assert.equal(catalog.items[0].pricePoints, 4);
  assert.equal(catalog.items[0].canEnable, true);
  assert.deepEqual(catalog.items[0].blockers, []);
  assert.equal(catalog.items[0].capabilitySource, "Relay 能力与精确路由证据已就绪");
});

test("personal retail blockers never disguise draft or unapproved models as distributable", () => {
  const [row] = normalizePersonalModelGrantCatalog([{
    ...grant,
    model_status: "draft",
    relay_capability_revision: null,
    price_per_item_points: null,
    effective_capabilities: { schema_version: 1, modes: {} },
  }], [publishedModel]).items;
  assert.equal(row.canEnable, false);
  assert.deepEqual(row.blockers, [
    "model_unpublished",
    "relay_unapproved",
    "capability_empty",
    "route_evidence_not_ready",
    "price_missing",
  ]);
});

test("personal grant mutation is mode-safe, reasoned, and quote-revision fenced", () => {
  assert.deepEqual(buildPersonalModelGrantPayload({
    grant: { ...grant, model: publishedModel, billingMode: "per_item" },
    enabled: true,
    pricePoints: "6",
    reason: "首批个人用户灰度开通",
  }), {
    expectedCapabilityVersion: 3,
    expectedQuoteRevision: "quote-v7",
    enabled: true,
    pricePerSecondPoints: null,
    pricePerItemPoints: 6,
    configOverride: {},
    reason: "首批个人用户灰度开通",
  });
  assert.throws(
    () => buildPersonalModelGrantPayload({ grant: { ...grant, billingMode: "per_item" }, enabled: true, pricePoints: 0, reason: "有效原因" }),
    /大于 0/,
  );
  assert.throws(
    () => buildPersonalModelGrantPayload({ grant: { ...grant, billingMode: "per_item" }, enabled: true, pricePoints: 2, reason: "短" }),
    /至少 3 个字符/,
  );
  assert.throws(
    () => buildPersonalModelGrantPayload({ grant: { ...grant, billingMode: "per_item" }, enabled: true, pricePoints: 2, reason: "🎬🚀" }),
    /至少 3 个字符/,
  );
});

test("platform client reads and writes the real personal-model-grants contract", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    accessToken: "owner-token",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify([]), { status: 200, headers: { "content-type": "application/json" } });
    },
  });
  await client.listAdminPersonalModelGrants();
  await client.upsertAdminPersonalModelGrant("model/a", {
    expectedCapabilityVersion: 3,
    expectedQuoteRevision: "quote-v7",
    enabled: true,
    pricePerSecondPoints: null,
    pricePerItemPoints: 6,
    configOverride: {},
    reason: "首批个人用户灰度开通",
  });
  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/personal-model-grants",
    "https://platform.example/api/v1/platform-admin/personal-model-grants/model%2Fa",
  ]);
  assert.deepEqual(JSON.parse(captured[1].options.body), {
    expected_capability_version: 3,
    expected_quote_revision: "quote-v7",
    enabled: true,
    price_per_second_points: null,
    price_per_item_points: 6,
    config_override: {},
    reason: "首批个人用户灰度开通",
  });
});

test("personal distribution UI is non-mock, audited, conflict-aware, and keyboard bounded", () => {
  assert.match(management, /client\.listAdminPersonalModelGrants/);
  assert.match(management, /client\.upsertAdminPersonalModelGrant/);
  assert.match(component, /个人零售分发/);
  assert.match(component, /演示模式不展示虚构个人分发/);
  assert.match(component, /变更原因/);
  assert.match(component, /status === 403[\s\S]*status === 409[\s\S]*status === 422/);
  assert.match(component, /event\.key === "Escape"/);
  assert.match(component, /event\.key !== "Tab"/);
  assert.match(component, /role="dialog"/);
  assert.match(component, /aria-modal="true"/);
  assert.match(css, /\.personal-model-distribution-list > article/);
  assert.match(css, /\.personal-grant-editor-evidence/);
  assert.match(mobileCss, /\.personal-grant-editor \{ width: 100vw;[\s\S]*height: 100dvh/);
  assert.match(mobileCss, /@media \(max-width: 390px\)[\s\S]*\.personal-model-distribution-list > article \{ grid-template-columns: minmax\(0, 1fr\)/);
});

test("personal batch execution reuses one preview-bound idempotency key after an unknown response", () => {
  assert.match(component, /const batchIdempotencyKeyRef = useRef\(""\)/);
  assert.match(
    component,
    /const result = await onPreview\(changes\(\)\);\s*batchIdempotencyKeyRef\.current = globalThis\.crypto\?\.randomUUID\?\.\(\)/,
  );
  assert.match(component, /idempotencyKey: batchIdempotencyKeyRef\.current/);
  assert.doesNotMatch(
    component,
    /const executeBatch = async \(\) => \{[\s\S]*?idempotencyKey:\s*globalThis\.crypto\?\.randomUUID/,
  );
  assert.match(component, /\[enabled, pricePoints, reason\]/);
});
