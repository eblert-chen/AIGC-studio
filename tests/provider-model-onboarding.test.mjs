import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  APPLY_CONFIRMATION,
  ECB_DAILY_XML_URL,
  OnboardingError,
  buildCnyIdentityEvidence,
  buildCommercialReleaseRequest,
  buildProviderCostRateSets,
  loadPricingReview,
  loadReviewedCandidates,
  parseEcbDailyXml,
  parsePrivateEnv,
  runFormalRelayProviderOnboarding,
  runLocalPlatformOwnerResolver,
  validateIntent,
} from "../scripts/provider-model-onboarding.mjs";

const workspace = new URL("..", import.meta.url);
const script = readFileSync(
  new URL("../scripts/provider-model-onboarding.mjs", import.meta.url),
  "utf8",
);
const intentExample = JSON.parse(
  readFileSync(
    new URL("../deploy/provider-model-onboarding.intent.example.json", import.meta.url),
    "utf8",
  ),
);
const envExample = readFileSync(new URL("../.env.example", import.meta.url), "utf8");
const compose = readFileSync(new URL("../docker-compose.yml", import.meta.url), "utf8");
const packageJson = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);

const SHA256 = /^[0-9a-f]{64}$/;
const PROVIDER_KEYS = [
  "MODEL_ONBOARDING_GOOGLE_API_KEY",
  "MODEL_ONBOARDING_MINIMAX_API_KEY",
  "MODEL_ONBOARDING_ARK_API_KEY",
];
const FORMER_CONTROL_TOKENS = [
  "MODEL_ONBOARDING_RELAY_ADMIN_TOKEN",
  "MODEL_ONBOARDING_PLATFORM_OWNER_TOKEN",
  "MODEL_ONBOARDING_CUSTOMER_TOKEN",
];

function clone(value) {
  return structuredClone(value);
}

function focusedIntent(modelIDs, mutate = () => {}) {
  const document = clone(intentExample);
  const enabled = new Set(modelIDs);
  document.models = document.models.filter((model) => enabled.has(model.public_model_id));
  mutate(document);
  return document;
}

function declaration({
  routeID,
  channelID,
  providerName,
  upstreamModel,
  publicModelID,
  mode,
  resolutions,
  maxImages = 0,
  maxVideos = 0,
  maxAudio = 0,
  outputCounts = [1],
}) {
  return {
    route_id: routeID,
    channel_id: channelID,
    provider_name: providerName,
    upstream_model: upstreamModel,
    capabilities: {
      schema_version: 1,
      modes: {
        [mode]: {
          limits: {
            resolutions,
            max_images: maxImages,
            max_videos: maxVideos,
            max_audio: maxAudio,
            output_counts: outputCounts,
          },
        },
      },
    },
    model_release: { public_model_id: publicModelID },
  };
}

function formalReceipt(phase) {
  if (phase === "check-credential") {
    return {
      provider: "google-gemini-api",
      account_id: "primary",
      channel_id: 990001,
      public_model_ids: ["veo-3.1"],
      credential_available: true,
    };
  }
  if (phase === "plan-routes") {
    return {
      "veo-3.1": [
        declaration({
          routeID: "google-veo-3-1-primary",
          channelID: 990001,
          providerName: "google-gemini-api",
          upstreamModel: "veo-3.1-generate-preview",
          publicModelID: "veo-3.1",
          mode: "text_to_video",
          resolutions: ["720p"],
        }),
      ],
    };
  }
  return {
    channel_id: phase === "stage" ? 990002 : 990001,
    provider: phase === "stage" ? "minimax" : "google-gemini-api",
    account_id: "provider-account",
    public_model_ids: [phase === "stage" ? "minimax-h3" : "veo-3.1"],
    route_ids: phase === "stage" ? null : ["google-veo-3-1-primary"],
    channel_created: phase === "stage",
    route_test_ready: phase === "finalize",
    native_abilities: "disabled_until_platform_publication",
  };
}

function formalOnboardingRunner(invocations, receiptForPhase = formalReceipt) {
  return (file, args, options) => {
    invocations.push({ file, args, options });
    if (args[0] === "compose" && args.includes("images")) {
      return { status: 0, stdout: `sha256:${"b".repeat(64)}\n`, stderr: "" };
    }
    if (args[0] === "volume" && args[1] === "create") {
      return { status: 0, stdout: `${args.at(-1)}\n`, stderr: "" };
    }
    if (args[0] === "run") {
      return { status: 0, stdout: "", stderr: "" };
    }
    if (args[0] === "compose" && args.includes("/relay-provider-onboarding")) {
      const phase = args[args.indexOf("--phase") + 1];
      return {
        status: 0,
        stdout: JSON.stringify(receiptForPhase(phase)),
        stderr: "",
      };
    }
    if (args[0] === "volume" && args[1] === "rm") {
      return { status: 0, stdout: `${args.at(-1)}\n`, stderr: "" };
    }
    assert.fail(`unexpected Docker invocation: ${args.join(" ")}`);
  };
}

function withPrivateFormalInputs(callback) {
  const privateDirectory = new URL(
    `../deploy/secrets/provider-onboarding-test-${randomUUID()}/`,
    import.meta.url,
  );
  mkdirSync(privateDirectory, { recursive: true });
  const credential = new URL("credential.json", privateDirectory);
  const currentRoutes = new URL("current-routes.json", privateDirectory);
  const signedRoutes = new URL("signed-routes.json", privateDirectory);
  const secret = "provider-secret-that-must-never-enter-argv";
  writeFileSync(credential, JSON.stringify({ schema_version: 1, api_key: secret }), {
    mode: 0o600,
  });
  writeFileSync(currentRoutes, JSON.stringify({}), { mode: 0o600 });
  writeFileSync(signedRoutes, JSON.stringify({ "veo-3.1": [{}] }), { mode: 0o600 });
  try {
    return callback({ credential, currentRoutes, signedRoutes, secret });
  } finally {
    rmSync(privateDirectory, { recursive: true, force: true });
  }
}

test("schema-v2 plan contains all twelve reviewed models and no provider secret", () => {
  const sentinel = "sentinel-provider-secret-must-not-appear";
  const output = execFileSync(
    process.execPath,
    ["scripts/provider-model-onboarding.mjs", "plan"],
    {
      cwd: workspace,
      encoding: "utf8",
      env: { ...process.env, MODEL_ONBOARDING_GOOGLE_API_KEY: sentinel },
    },
  );
  const plan = JSON.parse(output);
  assert.equal(intentExample.schema_version, 2);
  assert.equal(plan.status, "PLAN_ONLY");
  assert.equal(plan.real_acceptance, false);
  assert.equal(plan.mutating, false);
  assert.equal(plan.points_per_cny, 10);
  assert.equal(plan.reviewed_at, "2026-09-02");
  assert.equal(Object.hasOwn(plan, "required_private_variables"), false);
  assert.ok(
    plan.required_operator_inputs.some((item) =>
      item.includes("New API > 供应商接入"),
    ),
  );
  assert.equal(plan.reviewed_candidates.length, 12);
  assert.deepEqual(
    plan.reviewed_candidates.map((model) => model.public_model_id).sort(),
    intentExample.models.map((model) => model.public_model_id).sort(),
  );
  assert.ok(
    intentExample.models.every(
      (model) =>
        model.route_enabled === true &&
        model.commercial_policy === "automatic_when_cost_ceiling_is_qualified" &&
        !Object.hasOwn(model, "personal_price_points") &&
        !Object.hasOwn(model, "enterprise_price_points") &&
        !Object.hasOwn(model, "provider_cost_formula") &&
        !Object.hasOwn(model, "provider_cost_evidence"),
    ),
  );
  assert.doesNotMatch(output, new RegExp(sentinel));
});

test("schema-v2 intent selects repository-reviewed automatic pricing", () => {
  const pricing = loadPricingReview();
  const validated = validateIntent(intentExample, loadReviewedCandidates(), pricing);
  assert.equal(validated.enabled.length, 12);
  assert.equal(validated.providers.length, 3);
  assert.equal(validated.document.commercial_release.points_per_cny, 10);
  assert.equal(
    validated.document.commercial_release.pricing_policy,
    "provider_cost_plus_30_percent_margin_ceil_to_point",
  );
  assert.ok(validated.enabled.some((model) => model.commercially_qualified));
  assert.ok(validated.enabled.some((model) => !model.commercially_qualified));
});

test("private env parser rejects expansion and duplicate definitions", () => {
  assert.deepEqual(
    { ...parsePrivateEnv("A_TOKEN='literal-value-123456'\nB_TOKEN=second-value-123456\n") },
    { A_TOKEN: "literal-value-123456", B_TOKEN: "second-value-123456" },
  );
  assert.throws(
    () => parsePrivateEnv("A_TOKEN=first-value-123456\nA_TOKEN=second-value-123456\n"),
    (error) => error instanceof OnboardingError && error.code === "PRIVATE_ENV_DUPLICATE",
  );
  assert.throws(
    () => parsePrivateEnv("A_TOKEN=${LEAK}\n"),
    (error) =>
      error instanceof OnboardingError &&
      error.code === "PRIVATE_ENV_EXPANSION_FORBIDDEN",
  );
});

test("legacy break-glass runtime template contains exactly three empty provider keys", () => {
  const templatePath = new URL(
    "../deploy/provider-model-onboarding.runtime.env.example",
    import.meta.url,
  );
  assert.equal(existsSync(templatePath), true, "public provider runtime template is missing");
  const template = readFileSync(templatePath, "utf8");
  const assignments = template
    .split(/\r?\n/)
    .map((line) => /^([A-Z][A-Z0-9_]*)=(.*)$/.exec(line))
    .filter(Boolean);
  const providerAssignments = assignments.filter(([_, name]) =>
    PROVIDER_KEYS.includes(name),
  );
  assert.deepEqual(
    providerAssignments.map(([_, name]) => name).sort(),
    [...PROVIDER_KEYS].sort(),
  );
  assert.ok(providerAssignments.every(([_, __, value]) => value === ""));
  for (const token of FORMER_CONTROL_TOKENS) {
    assert.doesNotMatch(template, new RegExp(`^${token}=`, "m"));
  }
});

test("ECB evidence records exact source quotes, hash, and rational cross rate", () => {
  const xml = Buffer.from(
    "<Envelope><Cube><Cube time='2026-09-01'><Cube currency='USD' rate='1.1590'/><Cube currency='CNY' rate='7.8251'/></Cube></Cube></Envelope>",
  );
  const evidence = parseEcbDailyXml(xml, new Date("2026-09-02T12:00:00Z"), 5);
  assert.equal(evidence.kind, "ecb_usd_cny_cross_rate_evidence");
  assert.equal(evidence.source_url, ECB_DAILY_XML_URL);
  assert.equal(evidence.observed_date, "2026-09-01");
  assert.deepEqual(evidence.source_quotes_per_eur, { USD: "1.1590", CNY: "7.8251" });
  assert.match(evidence.response_sha256, SHA256);
  assert.equal(evidence.usd_to_cny.formula, "CNY_per_EUR / USD_per_EUR");
  assert.equal(
    BigInt(evidence.usd_to_cny.numerator) * 11590n,
    BigInt(evidence.usd_to_cny.denominator) * 78251n,
  );
  assert.throws(
    () => parseEcbDailyXml(xml, new Date("2026-09-10T00:00:00Z"), 5),
    (error) => error instanceof OnboardingError && error.code === "FX_STALE",
  );
});

test("CNY official prices use an exact identity snapshot without ECB", () => {
  const evidence = buildCnyIdentityEvidence(loadPricingReview());
  assert.equal(evidence.currency, "CNY");
  assert.equal(evidence.cny_micros_per_currency_unit, 1_000_000);
  assert.deepEqual(evidence.rational, { numerator: "1", denominator: "1" });
  assert.match(evidence.source_document_sha256, SHA256);
  assert.match(evidence.evidence_sha256, SHA256);
});

test("MiniMax global endpoint remains fail-closed without an official rate", () => {
  const pricing = loadPricingReview();
  const document = focusedIntent(["minimax-h3-max", "seedream-5"], (intent) => {
    intent.providers.minimax.base_url = "https://api.minimax.io";
    intent.providers.minimax.region = "global";
  });
  const validated = validateIntent(document, loadReviewedCandidates(), pricing);
  const h3Max = validated.enabled.find((model) => model.public_model_id === "minimax-h3-max");
  assert.equal(h3Max.pricing.currency, "USD");
  assert.equal(h3Max.pricing.status, "contract_rate_required");
  assert.deepEqual(h3Max.pricing.rates, []);
  assert.equal(h3Max.commercially_qualified, false);
  assert.match(h3Max.commercial_blocker, /contract|invoice/i);

  const routes = {
    "minimax-h3-max": [
      declaration({
        routeID: "minimax-h3-max-primary",
        channelID: 990002,
        providerName: "minimax",
        upstreamModel: h3Max.candidate.provider_model_id,
        publicModelID: "minimax-h3-max",
        mode: "text_to_video",
        resolutions: ["480p"],
      }),
    ],
    "seedream-5": [
      declaration({
        routeID: "seedream-5-primary",
        channelID: 990003,
        providerName: "volcengine-ark",
        upstreamModel: validated.enabled.find((model) => model.public_model_id === "seedream-5")
          .candidate.provider_model_id,
        publicModelID: "seedream-5",
        mode: "text_to_image",
        resolutions: ["2k"],
      }),
    ],
  };
  assert.throws(
    () =>
      buildProviderCostRateSets({
        intent: validated,
        routes,
        pricing,
        fx: parseEcbDailyXml(
          Buffer.from(
            "<Envelope><Cube><Cube time='2026-09-01'><Cube currency='USD' rate='1.1590'/><Cube currency='CNY' rate='7.8251'/></Cube></Cube></Envelope>",
          ),
          new Date("2026-09-02T00:00:00Z"),
          5,
        ),
      }),
    (error) =>
      error instanceof OnboardingError && error.code === "PRICING_RECTANGLE_MISSING",
  );
});

test("Seedance 2.5 records exact 42/70 token rates and stays commercially blocked", () => {
  const pricing = loadPricingReview();
  const fact = pricing.facts.find((item) => item.public_model_id === "seedance-2.5");
  assert.deepEqual(
    fact.rates.map((rate) => ({
      input_video_present: rate.input_video_present,
      unit: rate.unit,
      amount: rate.amount,
    })),
    [
      { input_video_present: true, unit: "million_tokens", amount: "42" },
      { input_video_present: false, unit: "million_tokens", amount: "70" },
    ],
  );
  assert.match(fact.commercial_blocker, /server-enforced maximum total-token/i);
  const validated = validateIntent(intentExample, loadReviewedCandidates(), pricing);
  const model = validated.enabled.find((item) => item.public_model_id === "seedance-2.5");
  assert.equal(model.commercially_qualified, false);
  assert.equal(model.commercial_blocker, fact.commercial_blocker);
});

test("formal Relay runner covers stage, plan-routes, and finalize without credentials in argv", () => {
  withPrivateFormalInputs(({ credential, signedRoutes, secret }) => {
    const invocations = [];
    const runner = formalOnboardingRunner(invocations);
    const common = {
      environment: "development",
      credentialFile: fileURLToPath(credential),
      dockerExecutable: "docker-test",
      runner,
    };
    const staged = runFormalRelayProviderOnboarding({ ...common, phase: "stage" });
    const planned = runFormalRelayProviderOnboarding({
      ...common,
      phase: "plan-routes",
      replaceAllRoutes: true,
      createdAt: "2026-09-02T00:00:00Z",
      createdBy: "platform-owner-test",
      reason: "reviewed provider onboarding test",
      rpmLimit: 6,
      activeTaskLimit: 2,
    });
    const finalized = runFormalRelayProviderOnboarding({
      ...common,
      phase: "finalize",
      signedRoutesFile: fileURLToPath(signedRoutes),
    });
    assert.equal(staged.route_test_ready, false);
    assert.deepEqual(Object.keys(planned), ["veo-3.1"]);
    assert.equal(finalized.route_test_ready, true);
    assert.ok(invocations.every((invocation) => invocation.file === "docker-test"));
    assert.ok(invocations.every((invocation) => invocation.options.shell === false));
    const argv = invocations.flatMap((invocation) => invocation.args).join(" ");
    assert.doesNotMatch(argv, new RegExp(secret));
    assert.match(argv, /--phase stage/);
    assert.match(argv, /--phase plan-routes/);
    assert.match(argv, /--phase finalize/);
    assert.match(argv, /--replace-all-routes/);
    assert.match(argv, /--signed-routes \/run\/provider-onboarding\/signed-routes\.json/);
    assert.match(argv, /--created-by platform-owner-test/);
    assert.match(argv, /--rpm-limit 6/);
    assert.equal(
      invocations.filter(
        (invocation) =>
          invocation.args[0] === "volume" && invocation.args[1] === "rm",
      ).length,
      3,
    );
  });
});

test("formal Relay runner defaults to New API managed credentials without a host credential file", () => {
  withPrivateFormalInputs(({ currentRoutes, signedRoutes }) => {
    const invocations = [];
    const runner = formalOnboardingRunner(invocations);
    const common = {
      environment: "development",
      managedProvider: "google-gemini-api",
      dockerExecutable: "docker-test",
      runner,
    };
    runFormalRelayProviderOnboarding({ ...common, phase: "check-credential" });
    runFormalRelayProviderOnboarding({ ...common, phase: "stage" });
    runFormalRelayProviderOnboarding({
      ...common,
      phase: "plan-routes",
      currentRoutesFile: fileURLToPath(currentRoutes),
      createdAt: "2026-09-02T00:00:00Z",
      createdBy: "platform-owner-test",
      reason: "reviewed UI-managed provider onboarding test",
      rpmLimit: 6,
      activeTaskLimit: 2,
    });
    runFormalRelayProviderOnboarding({
      ...common,
      phase: "finalize",
      signedRoutesFile: fileURLToPath(signedRoutes),
    });

    const formal = invocations.filter(
      (invocation) =>
        invocation.args[0] === "compose" &&
        invocation.args.includes("/relay-provider-onboarding"),
    );
    assert.equal(formal.length, 4);
    assert.ok(
      formal.every((invocation) =>
        invocation.args.join(" ").includes("--managed-provider google-gemini-api"),
      ),
    );
    assert.ok(
      formal.every((invocation) =>
        invocation.args.join(" ").includes("--env RELAY_PROVIDER_ONBOARDING_FILE="),
      ),
    );
    assert.ok(
      invocations.every(
        (invocation) => !invocation.args.join(" ").includes("/input/credential.json"),
      ),
    );
    assert.ok(
      invocations.some((invocation) =>
        invocation.args.join(" ").includes("/input/signed-routes.json"),
      ),
    );
    assert.ok(
      invocations.every((invocation) =>
        PROVIDER_KEYS.every((name) => !Object.hasOwn(invocation.options.env, name)),
      ),
    );
  });
});

test("formal Relay runner requires one exact credential source", () => {
  withPrivateFormalInputs(({ credential }) => {
    const noDocker = () => assert.fail("Docker must not run");
    assert.throws(
      () =>
        runFormalRelayProviderOnboarding({
          environment: "development",
          phase: "stage",
          runner: noDocker,
        }),
      (error) =>
        error instanceof OnboardingError &&
        error.code === "RELAY_ONBOARDING_CREDENTIAL_SOURCE_INVALID",
    );
    assert.throws(
      () =>
        runFormalRelayProviderOnboarding({
          environment: "development",
          phase: "stage",
          managedProvider: "minimax",
          credentialFile: fileURLToPath(credential),
          runner: noDocker,
        }),
      (error) =>
        error instanceof OnboardingError &&
        error.code === "RELAY_ONBOARDING_CREDENTIAL_SOURCE_INVALID",
    );
  });
});

test("formal route planning can seal an existing current inventory", () => {
  withPrivateFormalInputs(({ credential, currentRoutes }) => {
    const invocations = [];
    runFormalRelayProviderOnboarding({
      environment: "development",
      phase: "plan-routes",
      credentialFile: fileURLToPath(credential),
      currentRoutesFile: fileURLToPath(currentRoutes),
      createdAt: "2026-09-02T00:00:00Z",
      createdBy: "platform-owner-test",
      reason: "merge reviewed routes without deleting existing routes",
      rpmLimit: 6,
      activeTaskLimit: 2,
      dockerExecutable: "docker-test",
      runner: formalOnboardingRunner(invocations),
    });
    const formal = invocations.find((invocation) =>
      invocation.args.includes("/relay-provider-onboarding"),
    );
    assert.ok(formal);
    assert.match(
      formal.args.join(" "),
      /--current-routes \/run\/provider-onboarding\/signed-routes\.json/,
    );
    assert.doesNotMatch(formal.args.join(" "), /--replace-all-routes/);
  });
});

test("formal route planning accepts the Relay-reviewed historical profile without a release", () => {
  withPrivateFormalInputs(({ credential }) => {
    const plannedSeedream = declaration({
      routeID: "provider-onboarding-990003-seedream-5",
      channelID: 990003,
      providerName: "volcengine-ark",
      upstreamModel: "doubao-seedream-5-0-260128",
      publicModelID: "seedream-5",
      mode: "text_to_image",
      resolutions: ["2048x2048"],
    });
    delete plannedSeedream.model_release;
    const receipt = runFormalRelayProviderOnboarding({
      environment: "development",
      phase: "plan-routes",
      credentialFile: fileURLToPath(credential),
      replaceAllRoutes: true,
      createdAt: "2026-09-02T00:00:00Z",
      createdBy: "platform-owner-test",
      reason: "Relay-reviewed historical Seedream profile",
      rpmLimit: 6,
      activeTaskLimit: 2,
      dockerExecutable: "docker-test",
      runner: formalOnboardingRunner([], () => ({ "seedream-5": [plannedSeedream] })),
    });
    assert.equal(receipt["seedream-5"][0].model_release, undefined);
  });
});

test("formal stage accepts a byte-identical already-finalized channel as recovery", () => {
  withPrivateFormalInputs(({ credential }) => {
    const receipt = runFormalRelayProviderOnboarding({
      environment: "development",
      phase: "stage",
      credentialFile: fileURLToPath(credential),
      dockerExecutable: "docker-test",
      runner: formalOnboardingRunner([], (phase) => ({
        ...formalReceipt(phase),
        route_test_ready: true,
      })),
    });
    assert.equal(receipt.route_test_ready, true);
    assert.deepEqual(receipt.route_ids, null);
  });
});

test("local Platform owner resolver translates OIDC ownership inside the exact service image", () => {
  const invocations = [];
  const receipt = runLocalPlatformOwnerResolver({
    dockerExecutable: "docker-test",
    runner(file, args, options) {
      invocations.push({ file, args, options });
      return {
        status: 0,
        stdout: JSON.stringify({
          schema_version: 1,
          kind: "provider_model_onboarding_local_owner",
          user_id: "11111111-1111-4111-8111-111111111111",
          source: "configured_oidc_owner",
        }),
        stderr: "",
      };
    },
  });
  assert.equal(receipt.user_id, "11111111-1111-4111-8111-111111111111");
  assert.equal(invocations.length, 1);
  assert.equal(invocations[0].file, "docker-test");
  assert.equal(invocations[0].options.shell, false);
  assert.match(
    invocations[0].args.join(" "),
    /exec -T platform-api python -m platform_api\.provider_model_onboarding_control resolve-owner/,
  );
  assert.doesNotMatch(invocations[0].args.join(" "), /google-oauth2|X-Platform-Admin-User-ID/);
});

test("local Platform owner resolver reports a fresh database without inventing an identity", () => {
  const receipt = runLocalPlatformOwnerResolver({
    dockerExecutable: "docker-test",
    runner() {
      return {
        status: 3,
        stdout: "",
        stderr: '{"code":"LOCAL_OWNER_NOT_FOUND","status":"BLOCKED"}',
      };
    },
  });
  assert.equal(receipt, null);
});

test("formal Relay runner validates phase boundaries before invoking Docker", () => {
  assert.throws(
    () =>
      runFormalRelayProviderOnboarding({
        environment: "staging",
        phase: "plan-routes",
        credentialFile: "deploy/secrets/not-read.json",
        replaceAllRoutes: true,
        createdAt: "2026-09-02T00:00:00Z",
        createdBy: "platform-owner-test",
        reason: "must remain development-only",
        rpmLimit: 1,
        activeTaskLimit: 1,
        runner() {
          assert.fail("Docker must not run");
        },
      }),
    (error) =>
      error instanceof OnboardingError &&
      error.code === "RELAY_ONBOARDING_PLAN_ENVIRONMENT_INVALID",
  );
});

test("reviewed routes produce immutable rate sets and exact commercial release requests", () => {
  const pricing = loadPricingReview();
  const document = focusedIntent(["veo-3.1", "seedream-5"]);
  const intent = validateIntent(document, loadReviewedCandidates(), pricing);
  const veo = intent.enabled.find((model) => model.public_model_id === "veo-3.1");
  const seedream = intent.enabled.find((model) => model.public_model_id === "seedream-5");
  const routes = {
    "veo-3.1": [
      declaration({
        routeID: "google-veo-3-1-primary",
        channelID: 990001,
        providerName: "google-gemini-api",
        upstreamModel: veo.candidate.provider_model_id,
        publicModelID: "veo-3.1",
        mode: "text_to_video",
        resolutions: ["720p", "1080p", "4k"],
        maxImages: 3,
      }),
    ],
    "seedream-5": [
      declaration({
        routeID: "seedream-5-primary",
        channelID: 990003,
        providerName: "volcengine-ark",
        upstreamModel: seedream.candidate.provider_model_id,
        publicModelID: "seedream-5",
        mode: "text_to_image",
        resolutions: ["2k"],
        outputCounts: [1, 4],
      }),
    ],
  };
  const fx = parseEcbDailyXml(
    Buffer.from(
      "<Envelope><Cube><Cube time='2026-09-01'><Cube currency='USD' rate='1.1590'/><Cube currency='CNY' rate='7.8251'/></Cube></Cube></Envelope>",
    ),
    new Date("2026-09-02T00:00:00Z"),
    5,
  );
  fx.evidence_sha256 = "e".repeat(64);
  const cnyFx = buildCnyIdentityEvidence(pricing);
  const rateSets = buildProviderCostRateSets({ intent, routes, pricing, fx });
  assert.equal(rateSets.length, 4);
  assert.equal(new Set(rateSets.map((rateSet) => rateSet.id)).size, 4);
  assert.ok(rateSets.every((rateSet) => rateSet.source_document_sha256 === pricing.sha256));
  assert.ok(rateSets.every((rateSet) => rateSet.schema_version === 1));
  const veo4k = rateSets.find(
    (rateSet) => rateSet.upstream_model === veo.candidate.provider_model_id && rateSet.resolution === "4k",
  );
  assert.equal(veo4k.platform_billing_unit, "per_second");
  assert.equal(veo4k.currency, "USD");
  assert.deepEqual(veo4k.components, [
    {
      metric: "output_second",
      unit_amount_micros_numerator: 600000,
      unit_amount_micros_denominator: 1,
    },
  ]);
  const seedreamRate = rateSets.find(
    (rateSet) => rateSet.upstream_model === seedream.candidate.provider_model_id,
  );
  assert.equal(seedreamRate.platform_billing_unit, "per_item");
  assert.equal(seedreamRate.currency, "CNY");
  assert.deepEqual(seedreamRate.components, [
    {
      metric: "output_item",
      unit_amount_micros_numerator: 220000,
      unit_amount_micros_denominator: 1,
    },
  ]);

  const capabilityRevision = "a".repeat(64);
  const catalogRevision = "b".repeat(64);
  const veoRequest = buildCommercialReleaseRequest({
    model: {
      capability_version: 4,
      relay_capability_candidate_revision: capabilityRevision,
      relay_capability_candidate_catalog_revision: catalogRevision,
      relay_capability_candidate: routes["veo-3.1"][0].capabilities,
    },
    intentModel: veo,
    pricing,
    fx,
    cnyFx,
    reason: intent.reason,
    idempotencyPrefix: intent.idempotencyPrefix,
  });
  assert.equal(veoRequest.provider_cost_currency, "USD");
  assert.equal(veoRequest.provider_cost_formula.platform_billing_unit, "per_second");
  assert.deepEqual(veoRequest.provider_cost_formula.components, [
    {
      component: "output_second",
      rate_micros: 600000,
      quantity_numerator: 1,
      quantity_denominator: 1,
    },
  ]);
  assert.deepEqual(
    veoRequest.provider_cost_formula.assumptions.enforced_limits,
    {
      max_resolution: "4k",
      max_reference_images: 3,
      included_reference_images: 3,
    },
  );
  assert.equal(veoRequest.personal_price_points, null);
  assert.equal(veoRequest.enterprise_price_points, null);
  assert.match(veoRequest.idempotency_key, /^provider-onboarding-20260902-/);

  const seedreamRequest = buildCommercialReleaseRequest({
    model: {
      capability_version: 7,
      relay_capability_candidate_revision: capabilityRevision,
      relay_capability_candidate_catalog_revision: catalogRevision,
      relay_capability_candidate: routes["seedream-5"][0].capabilities,
    },
    intentModel: seedream,
    pricing,
    fx,
    cnyFx,
    reason: intent.reason,
    idempotencyPrefix: intent.idempotencyPrefix,
  });
  assert.equal(seedreamRequest.provider_cost_currency, "CNY");
  assert.equal(seedreamRequest.provider_cost_formula.platform_billing_unit, "per_item");
  assert.deepEqual(seedreamRequest.provider_cost_formula.components, [
    {
      component: "output_item",
      rate_micros: 220000,
      quantity_numerator: 1,
      quantity_denominator: 1,
    },
  ]);
  assert.deepEqual(seedreamRequest.provider_cost_formula.assumptions.enforced_limits, {
    max_output_count: 4,
  });
});

test("onboarding source uses the formal control planes and unique local startup entry", () => {
  assert.match(script, new RegExp(APPLY_CONFIRMATION));
  assert.doesNotMatch(script, /--(?:google|ark|minimax)-(?:api-)?key/i);
  assert.doesNotMatch(script, /\/api\/channel\//);
  assert.doesNotMatch(script, /PLATFORM_COMMERCIAL_RELEASE_API_PENDING/);
  for (const token of FORMER_CONTROL_TOKENS) {
    assert.doesNotMatch(script, new RegExp(token));
  }
  assert.match(script, /services:start:local/);
  assert.doesNotMatch(script, /docker\s+compose|compose\s+up/i);
  assert.match(script, /runFormalRelayProviderOnboarding\s*\(/);
  assert.match(script, /--managed-provider/);
  assert.match(script, /credentialMode:\s*cli\.credentialMode/);
});

test("Compose passes immutable provider rate sets to Relay", () => {
  assert.match(envExample, /^NEW_API_RELAY_PROVIDER_COST_RATE_SETS_JSON=$/m);
  assert.match(
    compose,
    /RELAY_PROVIDER_COST_RATE_SETS_JSON:\s*['"]?\$\{NEW_API_RELAY_PROVIDER_COST_RATE_SETS_JSON:-\}['"]?/,
  );
});

test("root package exposes only plan, check, and apply onboarding commands", () => {
  assert.equal(
    packageJson.scripts["models:onboard:plan"],
    "node scripts/provider-model-onboarding.mjs plan",
  );
  assert.equal(
    packageJson.scripts["models:onboard:check"],
    "node scripts/provider-model-onboarding.mjs check",
  );
  assert.equal(
    packageJson.scripts["models:onboard:apply"],
    "node scripts/provider-model-onboarding.mjs apply",
  );
});
