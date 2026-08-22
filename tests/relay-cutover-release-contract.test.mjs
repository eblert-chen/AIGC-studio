import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");
const docsDirectory = new URL("../docs/", import.meta.url);
const markdownPaths = (await readdir(docsDirectory, { recursive: true }))
  .filter((path) => path.endsWith(".md"))
  .sort();
const markdownDocuments = await Promise.all(
  markdownPaths.map(async (path) => [
    path,
    await readFile(new URL(path.replaceAll("\\", "/"), docsDirectory), "utf8"),
  ]),
);

const [
  readme,
  architecture,
  migration,
  deployment,
  production,
  releaseReadiness,
  workflow,
  acceptanceSource,
  acceptanceConfig,
  acceptanceTemplate,
  localSmoke,
] = await Promise.all([
  read("README.md"),
  read("docs/architecture.md"),
  read("docs/relay-new-api-migration.md"),
  read("docs/deployment-runbook.md"),
  read("docs/new-api-production-deployment.md"),
  read("docs/release-readiness.md"),
  read(".github/workflows/ci.yml"),
  read("scripts/relay-migration-acceptance.mjs"),
  read("docs/relay-migration-acceptance-config.example.json"),
  read("docs/relay-migration-acceptance-report-template.json"),
  read("scripts/smoke-local.ps1"),
]);

test("authoritative release docs freeze new-api as the only production Relay", () => {
  for (const [label, source] of [
    ["README", readme],
    ["architecture", architecture],
    ["migration", migration],
    ["deployment", deployment],
  ]) {
    assert.match(source, /new-api-v1\s*\/\s*generations\.v1/, `${label} omits the frozen backend identity`);
  }
  assert.match(migration, /不允许 Python\/new-api[^\n]*并行准入/);
  assert.match(production, /only active Relay/i);
  assert.match(readme, /Python Relay[\s\S]{0,100}离线 oracle artifact/);
  assert.match(readme, /0040_showcase_management/);
  assert.match(readme, /0039_new_api_relay_defaults/);
});

test("drain, affinity, rollback, and operations contracts cannot reactivate Python", () => {
  assert.match(migration, /legacy-default-v1[\s\S]*只能查询、审计/);
  assert.match(migration, /0039_new_api_relay_defaults[\s\S]*server default[\s\S]*不得[\s\S]*UPDATE/);
  assert.match(migration, /活动 task\/outbox[\s\S]*fail closed/);
  assert.match(migration, /submission_unknown[\s\S]*reconciliation_required[\s\S]*非终态/);
  assert.match(migration, /钱包预占已 settle\/release/);
  assert.match(migration, /上一版已验证、schema-compatible 的 new-api 不可变镜像/);
  assert.match(migration, /native new-api `\/channels`/);
  assert.match(migration, /不使用 iframe/);
  assert.match(migration, /URL 不携带/);
  assert.match(deployment, /不得重建 Python production admission/);
});

test("local acceptance targets the new-api and download-edge ports", () => {
  assert.match(localSmoke, /RelayBase = "http:\/\/127\.0\.0\.1:8300"/);
  assert.doesNotMatch(localSmoke, /127\.0\.0\.1:8100/);
  assert.match(deployment, /`8300`（new-api）/);
  assert.match(deployment, /`8400`（Download Edge）/);
});

test("current release material rejects stale Python-default vocabulary", () => {
  const authoritative = [
    readme,
    architecture,
    deployment,
    production,
    releaseReadiness,
    workflow,
    acceptanceSource,
  ].join("\n");
  for (const forbidden of [
    /Python Relay 回滚库/,
    /Python Relay 仍是默认/,
    /Python-Relay-first/i,
    /Python Relay must remain available/i,
    /python_relay_retirement_allowed/,
    /^  python-relay:\s*$/m,
    /--profile new-api-relay/,
  ]) {
    assert.doesNotMatch(authoritative, forbidden);
  }
});

test("the complete documentation tree rejects stale active-Python release claims", () => {
  const forbidden = [
    /Python Relay 仍是默认/,
    /Python-Relay-first/i,
    /Python Relay must remain available/i,
    /python_relay_retirement_allowed/,
    /--profile\s+python-relay-rollback/,
    /^##\s+后端双 Relay 地图\s*$/m,
    /new-api 只是可选候选/,
    /python\s+-m\s+relay_service\.provider_monitor_worker/,
    /providers\.verify\s+--production/,
    /alembic\s+upgrade\s+0012_generation_contract_v1/,
    /RELAY_PROVIDER_FACTORIES=/,
    /relay_service\.providers\.(?:kling|alibaba_wan|volcengine_ark):create_/,
  ];
  for (const [path, source] of markdownDocuments) {
    for (const pattern of forbidden) {
      assert.doesNotMatch(source, pattern, `${path} contains a stale production Relay claim`);
    }
  }
});

test("dated project snapshots identify themselves as non-release truth", () => {
  for (const path of [
    "project-completeness-2026-08-07.md",
    "project-tour-2026-08-12.md",
  ]) {
    const source = markdownDocuments.find(([candidate]) => candidate === path)?.[1];
    assert.ok(source, `missing archived snapshot ${path}`);
    assert.match(source.slice(0, 600), /归档快照（非发布真相）/);
  }
});

test("current navigation labels Python adapter and monitoring material as offline history", () => {
  assert.match(readme, /new-api 生产部署与渠道接入门禁/);
  assert.match(readme, /历史 Python 适配器合同（离线 oracle only）/);
  assert.match(readme, /历史 Python Provider 监控语义（离线 oracle only）/);
  assert.match(deployment, /历史 Python Provider 监控语义（离线 oracle only）/);
});

test("every retained Python channel guide is visibly frozen as an offline oracle", () => {
  for (const path of [
    "provider-adapter-v1.md",
    "official-provider-adapters.md",
    "provider-monitoring.md",
    "reverse-account-pool.md",
  ]) {
    const source = markdownDocuments.find(([candidate]) => candidate === path)?.[1];
    assert.ok(source, `missing retained oracle guide ${path}`);
    assert.match(source.slice(0, 800), /(?:离线 oracle|离线历史 oracle)/);
    assert.match(source.slice(0, 800), /(?:冻结|非生产|不是当前接入手册)/);
  }
});

test("offline oracle acceptance fails closed outside its isolated test role", () => {
  assert.match(acceptanceSource, /schemaVersion !== 2/);
  assert.match(acceptanceSource, /oracle\.mode !== "isolated_offline_oracle"/);
  assert.match(acceptanceSource, /oracle\.productionAdmissionAllowed !== false/);
  assert.match(acceptanceSource, /environmentClass === "production"/);
  assert.match(acceptanceSource, /active_production_relay: "new-api-v1"/);
  assert.match(acceptanceSource, /python_relay_production_admission_allowed: false/);

  const config = JSON.parse(acceptanceConfig);
  const template = JSON.parse(acceptanceTemplate);
  assert.equal(config.schemaVersion, 2);
  assert.deepEqual(
    {
      mode: config.oracle.mode,
      productionAdmissionAllowed: config.oracle.productionAdmissionAllowed,
    },
    { mode: "isolated_offline_oracle", productionAdmissionAllowed: false },
  );
  assert.equal(template.schema_version, 2);
  assert.equal(template.overall.active_production_relay, "new-api-v1");
  assert.equal(template.overall.python_relay_production_admission_allowed, false);
});

test("software cutover PASS does not fabricate external production evidence", () => {
  for (const boundary of ["Provider", "OBS", "IdP", "支付", "备份恢复", "容量"]) {
    assert.match(migration, new RegExp(boundary));
  }
  assert.match(migration, /BLOCKED\/NO-GO/);
  assert.match(releaseReadiness, /公网商用[^\n]*禁止放行/);
});
