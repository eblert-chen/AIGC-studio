import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const modelSigner = readFileSync(
  new URL("../backend/new-api-relay/cmd/relay-model-release-sign/main.go", import.meta.url),
  "utf8",
);
const releaseContract = readFileSync(
  new URL("../backend/new-api-relay/generationrelease/release.go", import.meta.url),
  "utf8",
);
const releaseScript = readFileSync(
  new URL(
    "../backend/new-api-relay/scripts/sign-paid-canary-model-route-release.ps1",
    import.meta.url,
  ),
  "utf8",
);
const pairPublisher = readFileSync(
  new URL(
    "../backend/new-api-relay/cmd/relay-release-pair-publish/main.go",
    import.meta.url,
  ),
  "utf8",
);
const relayDockerfile = readFileSync(
  new URL("../backend/new-api-relay/Dockerfile", import.meta.url),
  "utf8",
);
const modelSigningGuide = readFileSync(
  new URL(
    "../backend/new-api-relay/docs/platform-model-release-signing.md",
    import.meta.url,
  ),
  "utf8",
);

test("offline model-release signer keeps private material file-bound and validates strict input", () => {
  assert.match(modelSigner, /generationrelease\.DecodeStrict\(releaseBytes\)/);
  assert.match(modelSigner, /release\.Attestation != nil/);
  assert.match(modelSigner, /generationprofile\.Get\(release\.AdapterProfileID\)/);
  assert.match(modelSigner, /release\.Validate\(profile\)/);
  assert.match(modelSigner, /--private-key-file|"private-key-file"/);
  assert.doesNotMatch(modelSigner, /private-key"|private_key"|PRIVATE_KEY/);
  assert.match(modelSigner, /filepath\.IsAbs\(path\)/);
  assert.match(modelSigner, /os\.ModeSymlink/);
  assert.match(modelSigner, /os\.SameFile\(pathInfo, openedBefore\)/);
  assert.match(modelSigner, /os\.SameFile\(openedAfter, pathAfter\)/);
  assert.match(modelSigner, /runtime\.GOOS != "windows"[\s\S]*0o077/);
  assert.match(modelSigner, /clear\(privateKey\)/);
  assert.match(modelSigner, /Errors intentionally contain no private key bytes or release document/);
});

test("model-release lifetime is bounded in both signer and runtime verification contract", () => {
  assert.match(releaseContract, /MaximumAttestationLifetime\s*=\s*365 \* 24 \* time\.Hour/);
  assert.match(
    releaseContract,
    /notAfter\.Sub\(signedAt\) > MaximumAttestationLifetime/,
  );
  assert.match(modelSigner, /signedAt\.Before\(createdAt\)/);
  assert.match(
    modelSigner,
    /notAfter\.Sub\(signedAt\) > generationrelease\.MaximumAttestationLifetime/,
  );
  assert.match(modelSigner, /release\.VerifyAttestation/);
});

test("paid-canary release orchestration signs the model before route acceptance", () => {
  const modelSign = releaseScript.indexOf("go run ./cmd/relay-model-release-sign");
  const modelEmbed = releaseScript.indexOf("Add-Member -NotePropertyName model_release");
  const strictRouteValidation = releaseScript.indexOf("--validate-only");
  const routeSign = releaseScript.lastIndexOf("go run ./cmd/relay-route-acceptance-sign");
  const pairPublish = releaseScript.indexOf("go run ./cmd/relay-release-pair-publish");
  assert(strictRouteValidation >= 0, "reviewed routes must be strictly normalized before parsing");
  assert(modelSign >= 0, "missing model-release signing stage");
  assert(modelEmbed > modelSign, "signed model release must be embedded after signing");
  assert(routeSign > modelEmbed, "route acceptance must be signed after model embedding");
  assert(pairPublish > routeSign, "the validated pair must be published only after both signatures exist");
  assert.match(releaseScript, /\$modelIds\.Count -ne 1[\s\S]*"seedream-5"/);
  assert.match(releaseScript, /model_release\.attestation[\s\S]*declaration\.acceptance/);
  assert.match(releaseScript, /signed evidence instead of readiness booleans/);
  assert.match(releaseScript, /duplicate and unknown JSON keys/);
  assert.match(releaseScript, /\$legacyAliases\.Count -ne 1[\s\S]*"seedream-5-lite"/);
  assert.match(releaseScript, /must use different output paths/);
  assert.match(releaseScript, /\$modelReleaseExpiry -lt \$routeAcceptanceExpiry/);
  assert.match(releaseScript, /must not expire before route acceptance/);
  assert.match(releaseScript, /\$modelReleaseStart -gt \$routeAcceptanceStart/);
  assert.match(releaseScript, /must be active before route acceptance/);
  assert.doesNotMatch(releaseScript, /video\.seedance\.2/);
});

test("release pair publication is no-replace, durable, recoverable, and routes-last", () => {
  assert.match(pairPublisher, /os\.CreateTemp\(parent/);
  assert.match(pairPublisher, /syncFile:[\s\S]*file\.Sync\(\)/);
  assert.match(pairPublisher, /link:\s+os\.Link/);
  assert.match(pairPublisher, /commitStagedArtifact\([\s\S]*modelTemporary, options\.modelDestination/);
  assert.match(pairPublisher, /commitStagedArtifact\([\s\S]*routesTemporary, options\.routesDestination/);
  assert.match(pairPublisher, /operations\.link\(staged\.path, destination\)/);
  assert.match(pairPublisher, /signed-routes path is the commit marker|routes commit marker/i);
  assert.match(pairPublisher, /os\.SameFile\(committedIdentity, staged\.identity\)/);
  assert.match(pairPublisher, /os\.SameFile\(current, identity\)/);
  assert.match(pairPublisher, /verifyCommittedReleasePair\(/);
  assert.match(pairPublisher, /releasePairMaximumArtifactBytes/);
  assert.match(pairPublisher, /exact replay also re-establishes directory durability/);
  assert.match(pairPublisher, /LegacyPublicAliases\) != 1/);
});

test("signing tools stay outside the production Relay image and have an operator runbook", () => {
  assert.doesNotMatch(relayDockerfile, /relay-model-release-sign|relay-route-acceptance-sign|relay-release-pair-publish/);
  assert.match(modelSigningGuide, /first produce and embed|Sign that document/i);
  assert.match(modelSigningGuide, /relay-model-release-sign/);
  assert.match(modelSigningGuide, /relay-route-acceptance-sign/);
  assert.match(modelSigningGuide, /maximum 365 days|capped at 365 days/i);
  assert.match(modelSigningGuide, /do not carry draft models/i);
});
