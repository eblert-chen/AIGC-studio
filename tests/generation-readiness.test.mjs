import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  normalizeModeReadiness,
  resolveGenerationReadiness,
} from "../src/modelCapabilities.js";


const rawReady = {
  text_to_video: {
    default: {
      ready: true,
      status: "ready",
      blockers: [],
    },
    options: {
      face_enabled: {
        supported: true,
        ready: true,
        status: "ready",
        blockers: [],
      },
    },
  },
};


test("normalizes server-owned readiness and keeps human resource evidence", () => {
  const normalized = normalizeModeReadiness({
    text_to_video: {
      default: {
        ready: false,
        status: "blocked",
        blockers: [
          {
            code: "resource_grant_expired",
            resource_key: "feature.generation",
            resource_name: "企业生成能力",
          },
        ],
      },
      options: {
        face_enabled: {
          supported: true,
          ready: false,
          status: "blocked",
          blockers: [
            {
              code: "resource_not_granted",
              resource_key: "face.library",
              resource_name: "人物参考库",
            },
          ],
        },
      },
    },
  });

  assert.deepEqual(normalized.text_to_video.default.blockers, [
    {
      code: "resource_grant_expired",
      message: "",
      resourceKey: "feature.generation",
      resourceName: "企业生成能力",
      retryable: false,
    },
  ]);
  assert.deepEqual(normalized.text_to_video.options.faceEnabled.blockers, [
    {
      code: "resource_not_granted",
      message: "",
      resourceKey: "face.library",
      resourceName: "人物参考库",
      retryable: false,
    },
  ]);
});


test("default generation uses default readiness and face generation uses the conditional option", () => {
  const normalized = normalizeModeReadiness({
    ...rawReady,
    text_to_video: {
      ...rawReady.text_to_video,
      options: {
        face_enabled: {
          supported: true,
          ready: false,
          status: "blocked",
          blockers: [
            {
              code: "resource_not_granted",
              resource_key: "face.library",
              resource_name: "人物参考库",
            },
          ],
        },
      },
    },
  });

  assert.deepEqual(
    resolveGenerationReadiness(normalized, "text_to_video"),
    {
      status: "ready",
      ready: true,
      supported: true,
      blockers: [],
    },
  );
  assert.deepEqual(
    resolveGenerationReadiness(normalized, "text_to_video", {
      faceEnabled: true,
    }),
    {
      status: "blocked",
      ready: false,
      supported: true,
      blockers: [
        {
          code: "resource_not_granted",
          message: "",
          resourceKey: "face.library",
          resourceName: "人物参考库",
          retryable: false,
        },
      ],
    },
  );
});


test("missing readiness fails closed instead of claiming generation is ready", () => {
  const missing = resolveGenerationReadiness({}, "text_to_video");
  assert.equal(missing.status, "unverified");
  assert.equal(missing.ready, false);
});


test("unsupported face mode is explicit and never falls back to default readiness", () => {
  const normalized = normalizeModeReadiness({
    text_to_video: {
      default: { ready: true, status: "ready", blockers: [] },
      options: {
        face_enabled: {
          supported: false,
          ready: false,
          status: "unsupported",
          blockers: [],
        },
      },
    },
  });
  const readiness = resolveGenerationReadiness(
    normalized,
    "text_to_video",
    { faceEnabled: true },
  );
  assert.deepEqual(readiness, {
    status: "unsupported",
    ready: false,
    supported: false,
    blockers: [],
  });
});


test("development per-second demo models advertise one output in every mode", async () => {
  const demoSource = await readFile(
    new URL("../src/demo/studioDemoFixtures.js", import.meta.url),
    "utf8",
  );
  const demoCatalog = demoSource.match(
    /DEVELOPMENT_DEMO_MODEL_RESPONSES = import\.meta\.env\.PROD \? \[\] : \[([\s\S]*?)\n\];/,
  )?.[1] ?? "";
  const perSecondBlocks = [
    ...demoCatalog.matchAll(
      /pricing_mode:\s*"per_second",([\s\S]*?)(?=\n\s*\},\n\s*\{\n\s*id:|$)/g,
    ),
  ].map((match) => match[1]);

  assert.ok(perSecondBlocks.length > 0, "demo catalog includes a per-second model");
  for (const block of perSecondBlocks) {
    const outputDeclarations = [
      ...block.matchAll(/output_counts:\s*\[([^\]]*)\]/g),
    ].map((match) => match[1].replace(/\s/g, ""));
    assert.ok(outputDeclarations.length > 0);
    assert.deepEqual(
      [...new Set(outputDeclarations)],
      ["1"],
      "every per-second demo mode must mirror production's single-output rule",
    );
  }
});


test("development demo pricing uses duration or output count exactly once", async () => {
  const appSource = await readFile(
    new URL("../src/App.jsx", import.meta.url),
    "utf8",
  );
  const billingSource = await readFile(
    new URL("../src/billingPresentation.js", import.meta.url),
    "utf8",
  );

  assert.match(appSource, /const costPreview = generationCostPreview\(\{/);
  assert.match(billingSource, /model\.pricingMode === "per_second" \? duration : outputCount/);
  assert.match(billingSource, /const value = unitPrice \* quantity;/);
  assert.doesNotMatch(appSource, /duration \* model\.rate \* outputCount/);
});

test("model editor preserves conditional face resources instead of downgrading schema v2", async () => {
  const [supportSource, fieldsSource] = await Promise.all([
    readFile(
      new URL("../src/components/management/managementConsoleSupport.js", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../src/components/management/CapabilityEditorFields.jsx", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(
    supportSource,
    /conditionalRequiredResourceKeys:\s*\{[\s\S]*?faceEnabled:\s*supportsFace \? faceRequiredResourceKeys : \[\]/,
  );
  assert.match(
    fieldsSource,
    /name=\{`\$\{prefix\}\.faceRequiredResourceKeys`\}[\s\S]*?defaultValue=\{capability\.conditionalRequiredResourceKeys\?\.faceEnabled\?\.join\(", "\) \?\? ""\}/,
  );
  assert.match(fieldsSource, /仅在人脸开关打开时校验；关闭人脸不会被这项授权阻止。/);
});
