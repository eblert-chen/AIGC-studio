import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";
import {
  createDirectorDeskBinding,
  createDirectorDeskConnectMessage,
  createDirectorDeskPortMessage,
  createDirectorDeskSessionId,
  createDirectorDeskSessionMessage,
  DIRECTOR_DESK_PROTOCOL,
  DIRECTOR_DESK_MESSAGES,
  directorCapturePayloadToFiles,
  parseDirectorDeskPortMessage,
  parseDirectorDeskReadyMessage,
  parseDirectorDeskMessage,
  requireDirectorCaptureCommit,
} from "../src/components/director3d/directorBridge.js";
import {
  createDirectorChildEnvelope,
  DIRECTOR_HOST_MESSAGES,
  parseDirectorConnectEnvelope,
  parseDirectorHostEnvelope,
} from "../vendor/storyai-3d-director-desk/src/editor/io/hostProtocol.js";

const PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

function sceneSnapshot(backgroundColor = "#000000") {
  return {
    viewMode: "director",
    selectedObjectId: null,
    selectedObjectIds: [],
    selectedCrowdId: null,
    directorInspectorMode: "scene",
    transformMode: "translate",
    viewportAspectRatio: "auto",
    viewportRuleOfThirdsEnabled: false,
    viewportPanelsCollapsed: false,
    project: {
      version: 1,
      scene: { backgroundColor },
      assets: [],
      objects: [],
      cameras: [],
      activeCameraId: null,
      panoramaAssetId: null,
    },
  };
}

test("director build emits the StoryAI MIT notice as a browser-distributed asset", async () => {
  const [{ default: viteConfig }, vendorLicense] = await Promise.all([
    import(new URL("../vendor/storyai-3d-director-desk/vite.config.ts", import.meta.url)),
    readFile(new URL("../vendor/storyai-3d-director-desk/LICENSE", import.meta.url), "utf8"),
  ]);
  const licensePlugin = viteConfig.plugins
    .flat()
    .find((plugin) => plugin?.name === "storyai-third-party-license-notice");
  assert.ok(licensePlugin, "StoryAI license emission plugin must remain enabled");
  const emitted = [];
  licensePlugin.generateBundle.call({ emitFile: (asset) => emitted.push(asset) });
  assert.deepEqual(emitted, [
    {
      type: "asset",
      fileName: "THIRD_PARTY_LICENSES.txt",
      source: vendorLicense,
    },
  ]);
  assert.match(emitted[0].source, /MIT License/);
  assert.match(emitted[0].source, /Copyright \(c\) 2026 YZ/);
});

test("director session identity is stable and isolated by workspace and shot", () => {
  const first = createDirectorDeskSessionId("development:user-a:workspace-a", "shot-1");
  assert.equal(first, createDirectorDeskSessionId("development:user-a:workspace-a", "shot-1"));
  assert.notEqual(first, createDirectorDeskSessionId("development:user-a:workspace-a", "shot-2"));
  assert.notEqual(first, createDirectorDeskSessionId("production:user-a:workspace-a", "shot-1"));
  assert.match(first, /^xutian-director-v1:/);
  const binding = createDirectorDeskBinding(first, 7);
  const emptyMessage = createDirectorDeskSessionMessage(binding, 1, { snapshot: null, revision: 0 });
  assert.deepEqual(Object.keys(emptyMessage.payload).sort(), ["revision", "schemaVersion", "snapshot", "theme"]);
  assert.equal(emptyMessage.payload.snapshot, null);
  const snapshot = sceneSnapshot("#123456");
  const message = createDirectorDeskSessionMessage(binding, 2, { snapshot, revision: 4 });
  assert.equal(message.payload.theme, "light");
  assert.equal(message.payload.revision, 4);
  assert.deepEqual(message.payload.snapshot, snapshot);
  assert.notEqual(message.payload.snapshot, snapshot, "session snapshots must be cloned before crossing the port");
  assert.deepEqual(
    Object.keys(message).sort(),
    ["epoch", "instanceId", "payload", "protocolVersion", "sequence", "sessionNonce", "type"],
  );
  assert.doesNotMatch(JSON.stringify(message), /token|authorization|price|idempotency/i);
  assert.throws(
    () => createDirectorDeskSessionMessage(binding, 3, {
      snapshot: { ...sceneSnapshot(), authorization: "Bearer secret" },
      revision: 4,
    }),
    /不受支持的字段|凭据/,
  );
});

test("director bridge rejects wrong ready source, opaque origin, protocol, instance and stale port sequence", () => {
  const source = {};
  const instanceId = createDirectorDeskSessionId("development:user-a:workspace-a", "shot-1");
  const validReady = {
    source,
    origin: "null",
    data: {
      type: DIRECTOR_DESK_MESSAGES.ready,
      protocolVersion: DIRECTOR_DESK_PROTOCOL,
      instanceId,
    },
  };
  const readyOptions = { source, origin: "null", instanceId };
  assert.equal(parseDirectorDeskReadyMessage(validReady, readyOptions)?.type, DIRECTOR_DESK_MESSAGES.ready);
  assert.equal(parseDirectorDeskMessage(validReady, readyOptions)?.type, DIRECTOR_DESK_MESSAGES.ready);
  assert.equal(parseDirectorDeskReadyMessage(validReady, { ...readyOptions, source: {} }), null);
  assert.equal(parseDirectorDeskReadyMessage(validReady, { ...readyOptions, origin: "https://studio.example" }), null);
  assert.equal(parseDirectorDeskReadyMessage(validReady, { ...readyOptions, instanceId: "other" }), null);
  assert.equal(parseDirectorDeskReadyMessage({ ...validReady, data: { ...validReady.data, protocolVersion: 1 } }, readyOptions), null);

  const binding = createDirectorDeskBinding(instanceId, 1);
  const capture = createDirectorChildEnvelope(binding, DIRECTOR_HOST_MESSAGES.captures, 1, {
    requestId: "capture-1",
    captures: [{ dataUrl: PNG_DATA_URL, fileName: "shot.png" }],
  });
  const parsed = parseDirectorDeskPortMessage(capture, { binding, lastSequence: 0 });
  assert.equal(parsed?.type, DIRECTOR_DESK_MESSAGES.captures);
  assert.equal(parseDirectorDeskPortMessage(capture, { binding, lastSequence: 1 }), null, "replayed sequence must fail");
  assert.equal(parseDirectorDeskPortMessage({ ...capture, sessionNonce: "wrong" }, { binding, lastSequence: 0 }), null);
  assert.equal(parseDirectorDeskPortMessage({ ...capture, instanceId: "wrong" }, { binding, lastSequence: 0 }), null);
  assert.equal(parseDirectorDeskPortMessage({ ...capture, epoch: 2 }, { binding, lastSequence: 0 }), null);
  assert.equal(parseDirectorDeskPortMessage({ ...capture, protocolVersion: 1 }, { binding, lastSequence: 0 }), null);
  assert.equal(parseDirectorDeskPortMessage({ ...capture, payload: { requestId: "capture-1", captures: [] } }, { binding, lastSequence: 0 }), null);
});

test("vendor and host protocols use one authenticated MessageChannel and reject replacement/old bindings", async () => {
  const instanceId = createDirectorDeskSessionId("development:user-a:workspace-a", "shot-1");
  const first = createDirectorDeskBinding(instanceId, 1);
  const second = createDirectorDeskBinding(instanceId, 2);
  assert.deepEqual(parseDirectorConnectEnvelope(createDirectorDeskConnectMessage(first), instanceId), first);
  assert.equal(parseDirectorConnectEnvelope({ ...createDirectorDeskConnectMessage(first), protocolVersion: 1 }, instanceId), null);
  assert.equal(parseDirectorConnectEnvelope(createDirectorDeskConnectMessage(first), "other"), null);

  const channel = new MessageChannel();
  const received = new Promise((resolve) => { channel.port1.onmessage = ({ data }) => resolve(data); });
  channel.port1.start();
  channel.port2.postMessage(createDirectorChildEnvelope(first, DIRECTOR_HOST_MESSAGES.sessionAck, 1, {
    accepted: true,
    schemaVersion: 1,
  }));
  const data = await received;
  assert.equal(parseDirectorDeskPortMessage(data, { binding: first, lastSequence: 0 })?.type, DIRECTOR_DESK_MESSAGES.sessionAck);
  assert.equal(parseDirectorDeskPortMessage(data, { binding: second, lastSequence: 0 }), null, "a replaced port binding must fail");

  const hostSession = createDirectorDeskSessionMessage(second, 1, { snapshot: null, revision: 0 });
  assert.equal(parseDirectorHostEnvelope(hostSession, { binding: second, lastSequence: 0 })?.type, DIRECTOR_HOST_MESSAGES.session);
  assert.equal(parseDirectorHostEnvelope(hostSession, { binding: first, lastSequence: 0 }), null);
  const hostAck = createDirectorDeskPortMessage(second, DIRECTOR_DESK_MESSAGES.captureResult, 2, {
    requestId: "capture-1", ok: true, addedCount: 1, message: "已加入",
  });
  assert.equal(parseDirectorHostEnvelope(hostAck, { binding: second, lastSequence: 1 })?.type, DIRECTOR_HOST_MESSAGES.captureResult);
  channel.port1.close();
  channel.port2.close();
});

test("director captures become real validated PNG Files before entering host inputs", () => {
  const [file] = directorCapturePayloadToFiles({
    captures: [{ dataUrl: PNG_DATA_URL, fileName: "镜头:01" }],
  });
  assert.equal(file.type, "image/png");
  assert.equal(file.name, "镜头-01.png");
  assert.ok(file.size > 8);
  assert.throws(
    () => directorCapturePayloadToFiles({ captures: [{ dataUrl: "data:text/plain;base64,SGVsbG8=" }] }),
    /只允许传回 PNG/,
  );
  assert.throws(
    () => directorCapturePayloadToFiles({ captures: [{ dataUrl: "data:image/png;base64,SGVsbG8=" }] }),
    /PNG 文件校验/,
  );
  assert.deepEqual(
    requireDirectorCaptureCommit({ ok: true, addedCount: 1, message: "已加入" }),
    { addedCount: 1, message: "已加入" },
  );
  assert.throws(
    () => requireDirectorCaptureCommit({ ok: false, addedCount: 0, message: "当前镜头已经切换" }),
    /当前镜头已经切换/,
  );
});

test("embedded StoryAI keeps production authority in the host and excludes unlicensed bundled models", async () => {
  const [host, vendorHost, app, vite, character, store, catalog, rootPackage, vendorStyles, vendorLicense] = await Promise.all([
    readFile(new URL("../src/components/director3d/StoryAiDirectorDesk.jsx", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/src/editor/io/hostBridge.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/App.jsx", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/vite.config.ts", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/src/editor/runtime/CharacterModel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/src/editor/store/directorStore.ts", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/src/editor/modelLibrary/modelLibraryCatalog.ts", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/src/styles/index.css", import.meta.url), "utf8"),
    readFile(new URL("../vendor/storyai-3d-director-desk/LICENSE", import.meta.url), "utf8"),
  ]);
  assert.match(host, /event\.source|iframeRef\.current\?\.contentWindow|parseDirectorDeskMessage/);
  assert.match(vendorHost, /event\.source !== window\.parent/);
  assert.match(app, /captureContext\.shotId === binding\?\.entryId/);
  assert.match(app, /return handleFiles\("image", captureFiles\)/);
  assert.match(host, /requireDirectorCaptureCommit/);
  assert.doesNotMatch(host, /createTask|authorization|idempotency|quote/);
  assert.match(vite, /base: "\/director-desk\/"/);
  assert.match(vite, /publicDir: false/);
  assert.match(vite, /fileName: "THIRD_PARTY_LICENSES\.txt"/);
  assert.match(vite, /source: storyAiLicenseNotice/);
  assert.match(vendorLicense, /MIT License/);
  assert.match(vendorLicense, /Copyright \(c\) 2026 YZ/);
  const phoneStyles = vendorStyles.slice(vendorStyles.indexOf("@media (max-width: 620px)"));
  assert.match(phoneStyles, /\.app-shell\.is-embedded \.top-bar\s*\{[\s\S]*?min-height:\s*52px;[\s\S]*?width:\s*min\(212px,\s*calc\(100vw - 24px\)\);/);
  assert.match(phoneStyles, /\.app-shell\.is-embedded \.mode-toggle\s*\{[\s\S]*?width:\s*100%;[\s\S]*?height:\s*52px;/);
  assert.match(phoneStyles, /\.app-shell\.is-embedded \.mode-toggle-button\s*\{[\s\S]*?min-height:\s*44px;/);
  assert.match(phoneStyles, /\.app-shell\.is-embedded button\s*\{[\s\S]*?min-height:\s*44px;/);
  assert.match(phoneStyles, /\.viewport-toolbar\s*\{[\s\S]*?right:\s*8px;[\s\S]*?left:\s*8px;[\s\S]*?max-width:\s*none;[\s\S]*?overflow-x:\s*auto;/);
  assert.match(phoneStyles, /\.viewport-toolbar button\s*\{[\s\S]*?min-width:\s*44px;[\s\S]*?min-height:\s*44px;/);
  assert.doesNotMatch(character, /UE4MannequinModel/);
  assert.match(store, /rigType: "mannequin"/);
  assert.doesNotMatch(store, /rigType: "ue4-mannequin"/);
  assert.match(store, /getLocalModelLibraryStorageKey\(\)/);
  assert.match(store, /LOCAL_MODEL_LIBRARY_STORAGE_KEY_PREFIX/);
  assert.deepEqual(JSON.parse(rootPackage).workspaces, ["vendor/storyai-3d-director-desk"]);
  assert.match(catalog, /ModelLibraryCategoryId = "my-models"/);
  assert.doesNotMatch(catalog, /import\.meta\.glob|模型库\//);
  await assert.rejects(
    access(new URL("../vendor/storyai-3d-director-desk/public/models/ue-mannequin-retopology.glb", import.meta.url)),
    { code: "ENOENT" },
  );
});
