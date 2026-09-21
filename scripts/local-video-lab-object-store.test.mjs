import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { createServer, request } from "node:http";
import { mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import test from "node:test";
import { authorizeLabObjectDownloadURL, createLabObjectStoreHandler, LAB_OBJECT_ENDPOINT, objectStoreOptionsFromEnvironment } from "./local-video-lab-object-store.mjs";

const KEY = "outputs/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333";
const BODY = Buffer.concat([Buffer.from("00000018667479706d703432000000006d70343269736f6d", "hex"), Buffer.from("verified-local-video-fixture")]);
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const optionsFor = (root) => ({ root, environment: "development", mode: "mock", stateId: "mock-" + "a".repeat(64),
  endpointHost: LAB_OBJECT_ENDPOINT, bucket: "video-mock-" + "b".repeat(20),
  accessKeyId: "LAB0123456789ABCDEF0123", secretAccessKey: "unit-local-object-secret-" + "c".repeat(40) });

// Independent SDK-v2-shaped signer: tests do not call a server signing helper.
function signedHeaders(options, method, key = KEY, { body, prefix = "x-amz-", date, versioning = false, metadata } = {}) {
  const headers = { date: date || new Date().toUTCString() };
  if (body !== undefined) Object.assign(headers, { "content-length": String(body.length), "content-type": "video/mp4",
    [`${prefix}meta-size-bytes`]: String(body.length), [`${prefix}meta-sha256`]: sha256(body), [`${prefix}acl`]: "private", ...metadata });
  const fields = { "content-md5": "", "content-type": headers["content-type"] || "", date: headers.date,
    ...Object.fromEntries(Object.entries(headers).filter(([name]) => name.startsWith(prefix))) };
  const canonical = Object.keys(fields).sort().map((name) => name.startsWith(prefix) ? `${name}:${fields[name]}` : fields[name]).join("\n");
  const signed = createHmac("sha1", options.secretAccessKey).update(`${method}\n${canonical}\n/${options.bucket}/${key}${versioning ? "?versioning" : ""}`).digest("base64");
  headers.authorization = `${prefix === "x-obs-" ? "OBS" : "AWS"} ${options.accessKeyId}:${signed}`;
  return headers;
}

function signedURL(options, { key = KEY, expiry = Math.floor(Date.now() / 1000) + 300, virtual = true, obs = false } = {}) {
  const signed = createHmac("sha1", options.secretAccessKey).update(`GET\n\n\n${expiry}\n/${options.bucket}/${key}`).digest("base64");
  const query = new URLSearchParams({ [obs ? "AccessKeyId" : "AWSAccessKeyId"]: options.accessKeyId, Expires: String(expiry), Signature: signed });
  return `https://${virtual ? options.bucket + "." : ""}${options.endpointHost}/${virtual ? "" : options.bucket + "/"}${key}?${query}`;
}

async function fixture(t, { mode = "mock", inputAssetRequest } = {}) {
  const root = await mkdtemp(join(tmpdir(), "ai-video-object-store-test-"));
  t.after(async () => {
    const absolute = resolve(root);
    assert.ok(absolute.startsWith(resolve(tmpdir()) + sep));
    assert.ok(absolute.includes("ai-video-object-store-test-"));
    await rm(absolute, { recursive: true, force: true });
  });
  const options = optionsFor(root);
  options.mode = mode; options.stateId = mode + "-" + "a".repeat(64); options.bucket = "video-" + mode + "-" + "b".repeat(20);
  const server = createServer(await createLabObjectStoreHandler(options, { inputAssetRequest }));
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  t.after(async () => { server.closeAllConnections(); await new Promise((done) => server.close(done)); });
  const send = (method, path, headers = {}, body) => new Promise((done, reject) => {
    const req = request({ host: "127.0.0.1", port: server.address().port, path, method,
      headers: { host: `${options.bucket}.${options.endpointHost}`, ...headers } }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => done({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
      response.on("error", reject);
    });
    req.on("error", reject); req.end(body);
  });
  const put = (body = BODY, prefix = "x-amz-") => send("PUT", `/${KEY}`, signedHeaders(options, "PUT", KEY, { body, prefix }), body);
  return { root, options, server, send, put };
}

test("actual bytes persist and SDK-v2 PUT/HEAD/GET signatures bind exact metadata", async (t) => {
  const f = await fixture(t);
  assert.equal((await f.put()).status, 200);
  const head = await f.send("HEAD", `/${KEY}`, signedHeaders(f.options, "HEAD"));
  assert.equal(head.status, 200);
  assert.equal(head.headers["content-type"], "video/mp4");
  assert.equal(head.headers["content-length"], String(BODY.length));
  assert.equal(head.headers["x-amz-meta-sha256"], sha256(BODY));
  assert.equal(head.headers["x-obs-meta-sha256"], sha256(BODY));
  assert.equal(head.body.length, 0);
  const url = new URL(signedURL(f.options));
  const get = await f.send("GET", url.pathname + url.search);
  assert.equal(get.status, 200);
  assert.deepEqual(get.body, BODY);
  assert.equal(get.headers["content-disposition"], 'inline; filename="artifact.mp4"');
  assert.equal(get.headers["cache-control"], "private, no-store, max-age=0");
  const disk = join(f.root, "objects", sha256(KEY) + ".blob");
  assert.deepEqual(await readFile(disk), BODY);
  const metadata = JSON.parse(await readFile(join(f.root, "objects", sha256(KEY) + ".json")));
  assert.equal(metadata.sha256, sha256(BODY));
  assert.equal(metadata.object_key, KEY);
  assert.equal(metadata.state_id, f.options.stateId);
  const reloaded = await createLabObjectStoreHandler(f.options);
  assert.equal(typeof reloaded, "function", "restart accepts only its unchanged durable binding");
});

test("OBS and AWS v2 variants, path-style signing and bucket health/versioning remain compatible", async (t) => {
  const f = await fixture(t);
  assert.equal((await f.put(BODY, "x-obs-")).status, 200);
  const bucket = await f.send("HEAD", "/", signedHeaders(f.options, "HEAD", ""));
  assert.equal(bucket.status, 200);
  const versioning = await f.send("GET", "/?versioning", signedHeaders(f.options, "GET", "", { versioning: true }));
  assert.equal(versioning.status, 200);
  assert.match(versioning.body.toString(), /<VersioningConfiguration\/>/);
  for (const obs of [false, true]) {
    const url = new URL(signedURL(f.options, { virtual: false, obs }));
    const get = await f.send("GET", url.pathname + url.search, { host: url.hostname });
    assert.equal(get.status, 200);
    assert.deepEqual(get.body, BODY);
    assert.equal(authorizeLabObjectDownloadURL(url.href, f.options).objectKey, KEY);
  }
  const health = await f.send("GET", "/health", { host: f.options.endpointHost });
  assert.equal(health.status, 200);
  const result = JSON.parse(health.body);
  assert.equal(result.production_ready, false);
  assert.equal(result.state_id, f.options.stateId);
  assert.ok(!health.body.includes(f.options.accessKeyId));
  assert.ok(!health.body.includes(f.options.secretAccessKey));
});

test("preview range and HEAD read only validated immutable bytes", async (t) => {
  const f = await fixture(t);
  await f.put();
  const url = new URL(signedURL(f.options));
  for (const [range, start, end] of [["bytes=0-9", 0, 9], ["bytes=10-", 10, BODY.length - 1], ["bytes=-7", BODY.length - 7, BODY.length - 1]]) {
    const result = await f.send("GET", url.pathname + url.search, { range });
    assert.equal(result.status, 206);
    assert.deepEqual(result.body, BODY.subarray(start, end + 1));
    assert.equal(result.headers["content-range"], `bytes ${start}-${end}/${BODY.length}`);
    assert.equal(result.headers["content-length"], String(end - start + 1));
  }
  const head = await f.send("HEAD", url.pathname + url.search);
  assert.equal(head.status, 200); assert.equal(head.body.length, 0);
  for (const range of ["bytes=999999-", "bytes=2-1", "bytes=-0", "bytes=0-1,4-6"]) {
    const result = await f.send("GET", url.pathname + url.search, { range });
    assert.equal(result.status, 416);
    assert.equal(result.headers["content-range"], `bytes */${BODY.length}`);
  }
});

test("bad signatures, cross-state URLs, expiry and alternate targets fail closed", async (t) => {
  const f = await fixture(t);
  await f.put();
  const good = signedURL(f.options);
  const badURLs = [good.replace("Signature=", "Signature=bad"), good + "&Signature=again", good + "&ignored=yes",
    signedURL(f.options, { expiry: Math.floor(Date.now() / 1000) - 1 }), signedURL(f.options, { expiry: Math.floor(Date.now() / 1000) + 7200 }),
    good.replace(f.options.bucket + ".", "wrong-bucket."), good.replace(f.options.endpointHost, "unrelated.example"),
    good.replace("https://", "http://"), good.replace("https://", "https://credential@"), good + "#fragment",
    good.replace("/outputs/", "/outputs%2f"), good.replace("/outputs/", "/outputs/../outputs/"),
    good.replace("https://", "https://").replace(f.options.endpointHost, f.options.endpointHost + ":444")];
  for (const bad of badURLs) assert.throws(() => authorizeLabObjectDownloadURL(bad, f.options));
  assert.throws(() => authorizeLabObjectDownloadURL(good, { ...f.options, secretAccessKey: "rotated-local-object-secret-" + "d".repeat(40) }));
  const unsigned = await f.send("GET", `/${KEY}`);
  assert.equal(unsigned.status, 403);
  const headers = signedHeaders(f.options, "HEAD", KEY, { date: new Date(Date.now() - 3600_000).toUTCString() });
  assert.equal((await f.send("HEAD", `/${KEY}`, headers)).status, 403);
  const url = new URL(good);
  assert.equal((await f.send("DELETE", url.pathname + url.search)).status, 403);
  assert.equal((await f.send("GET", url.pathname + url.search + "&AWSAccessKeyId=wrong")).status, 400);
});

test("corruption cannot be served, replay is idempotent, and a signed cleanup removes only its object", async (t) => {
  const f = await fixture(t);
  assert.equal((await f.put()).status, 200);
  assert.equal((await f.put()).status, 200);
  assert.equal((await f.put(Buffer.from("different immutable content"))).status, 409);
  const url = new URL(signedURL(f.options));
  assert.deepEqual((await f.send("GET", url.pathname + url.search)).body, BODY);
  const disk = join(f.root, "objects", sha256(KEY) + ".blob");
  await writeFile(disk, Buffer.alloc(BODY.length, 0));
  const corrupt = await f.send("GET", url.pathname + url.search);
  assert.equal(corrupt.status, 500);
  assert.match(corrupt.body.toString(), /ObjectIntegrityMismatch/);
  assert.equal((await f.send("DELETE", `/${KEY}`, signedHeaders(f.options, "DELETE"))).status, 204);
  assert.equal((await f.send("DELETE", `/${KEY}`, signedHeaders(f.options, "DELETE"))).status, 204);
  assert.equal((await f.send("GET", url.pathname + url.search)).status, 404);
  assert.deepEqual(await readdir(join(f.root, "objects")), []);
});

test("upload must match declared SHA and size; temporary rejected bytes are not published", async (t) => {
  const f = await fixture(t);
  const headers = signedHeaders(f.options, "PUT", KEY, { body: BODY, metadata: { "x-amz-meta-sha256": "d".repeat(64) } });
  const rejected = await f.send("PUT", `/${KEY}`, headers, BODY);
  assert.equal(rejected.status, 400);
  assert.deepEqual(await readdir(join(f.root, "objects")), []);
  const wrongSize = signedHeaders(f.options, "PUT", KEY, { body: BODY, metadata: { "x-amz-meta-size-bytes": "1" } });
  assert.equal((await f.send("PUT", `/${KEY}`, wrongSize, BODY)).status, 400);
});

test("identity, modes, filesystem links and ambient production cannot reuse object state", async (t) => {
  const f = await fixture(t);
  for (const changed of [
    { ...f.options, stateId: "mock-" + "e".repeat(64) },
    { ...f.options, secretAccessKey: "changed-object-store-secret-" + "e".repeat(40) },
    { ...f.options, mode: "live", stateId: "live-" + "e".repeat(64), bucket: "video-live-" + "e".repeat(20) },
    { ...f.options, environment: "production" },
    { ...f.options, endpointHost: "obs.real-provider.example" },
  ]) await assert.rejects(createLabObjectStoreHandler(changed));
  assert.throws(() => objectStoreOptionsFromEnvironment({ APP_ENV: "production" }));
  const foreign = await mkdtemp(join(tmpdir(), "ai-video-object-store-test-foreign-"));
  t.after(() => rm(foreign, { recursive: true, force: true }));
  await writeFile(join(foreign, "not-a-lab-file"), "retain me");
  await assert.rejects(createLabObjectStoreHandler(optionsFor(foreign)));
  if (process.platform !== "win32") {
    const linked = join(f.root, "linked");
    await symlink(foreign, linked, "dir");
    await assert.rejects(createLabObjectStoreHandler(optionsFor(linked)));
  }
});

const INPUT_ID = "44444444-4444-4444-8444-444444444444";
const INPUT_PATH = `/api/v1/input-assets/${INPUT_ID}/content`;
const INPUT_MAX = 64 * 1024 * 1024;
const inputPath = ({ expires = Math.floor(Date.now() / 1000) + 300, disposition = "inline", signature = "c".repeat(64) } = {}) =>
  `${INPUT_PATH}?expires=${expires}&disposition=${disposition}&signature=${signature}`;

async function inputFixture(t, responder, { mode = "mock" } = {}) {
  const requests = [], wire = [];
  const platform = createServer((incoming, response) => { wire.push({ method: incoming.method, url: incoming.url, headers: incoming.headers }); responder(incoming, response); });
  await new Promise((done) => platform.listen(0, "127.0.0.1", done));
  t.after(async () => { platform.closeAllConnections(); await new Promise((done) => platform.close(done)); });
  const inputAssetRequest = (options, done) => {
    requests.push(options);
    assert.equal(options.protocol, "http:");
    assert.equal(options.hostname, "platform-lab");
    assert.equal(options.port, 8000);
    assert.equal(options.method, "GET");
    assert.deepEqual(options.headers, { host: "platform-lab:8000", accept: "*/*", "accept-encoding": "identity" });
    // Only the test transport maps this fixed container destination to its
    // loopback oracle; the module exposes no runtime upstream URL setting.
    return request({ ...options, hostname: "127.0.0.1", port: platform.address().port }, done);
  };
  const f = await fixture(t, { mode, inputAssetRequest });
  const read = (method, path, headers = {}, body) => f.send(method, path, { host: LAB_OBJECT_ENDPOINT, ...headers }, body);
  return { ...f, requests, wire, read };
}

test("mock reference bridge preserves raw signed URL bytes and strips all caller credentials", async (t) => {
  const reference = Buffer.from([0, 255, 137, 80, 78, 71, 13, 10, 26, 10, 1, 128, 2, 254]);
  const expires = String(Math.floor(Date.now() / 1000) + 300);
  const signerSecret = "offline-platform-input-asset-signer-test-only";
  // Match FilesystemInputAssetSigner's documented message; the bridge is not
  // given this secret and cannot authorize, regenerate, or extend the URL.
  const signature = createHmac("sha256", signerSecret).update(`v1\n${INPUT_ID}\n${expires}\ninline`).digest("hex");
  const encodedSignature = [...signature].map((character) => "%" + character.charCodeAt(0).toString(16)).join("");
  const path = `${INPUT_PATH}?%73ignature=${encodedSignature}&disposition=%69nline&expires=%${expires.charCodeAt(0).toString(16)}${expires.slice(1)}`;
  const f = await inputFixture(t, (incoming, response) => {
    assert.equal(incoming.url, path, "query field order and percent-encoding reach Platform unchanged");
    const query = new URL(incoming.url, "http://platform-lab:8000").searchParams;
    assert.equal(query.get("signature"), createHmac("sha256", signerSecret).update(`v1\n${INPUT_ID}\n${query.get("expires")}\n${query.get("disposition")}`).digest("hex"));
    assert.ok(Number(query.get("expires")) > Math.floor(Date.now() / 1000));
    response.writeHead(200, { "Content-Type": "image/png", "Content-Length": String(reference.length),
      "Content-Disposition": "inline; filename*=UTF-8''%E5%9B%BE%E5%83%8F.png", "Set-Cookie": "must-not-leave-platform=true",
      "Authorization": "must-not-leave-platform", "X-Bootstrap-Key": "must-not-leave-platform" });
    response.end(reference);
  });
  for (const method of ["GET", "HEAD"]) {
    const result = await f.read(method, path, { cookie: "user=secret", authorization: "Bearer caller-secret",
      "proxy-authorization": "Basic caller-secret", "x-bootstrap-key": "bootstrap-secret", "x-platform-bootstrap-token": "other-secret",
      "x-forwarded-host": "attacker.example", "x-forwarded-proto": "https", "x-original-url": "http://169.254.169.254/latest/meta-data/",
      range: "bytes=1-2" });
    assert.equal(result.status, 200);
    assert.equal(result.headers["content-type"], "image/png");
    assert.equal(result.headers["content-length"], String(reference.length));
    assert.equal(result.headers["content-disposition"], "inline; filename*=UTF-8''%E5%9B%BE%E5%83%8F.png");
    assert.equal(result.headers["set-cookie"], undefined);
    assert.equal(result.headers.authorization, undefined);
    assert.equal(result.headers["x-bootstrap-key"], undefined);
    assert.equal(result.headers["cache-control"], "private, no-store, max-age=0");
    assert.deepEqual(result.body, method === "HEAD" ? Buffer.alloc(0) : reference);
  }
  assert.equal(f.requests.length, 2, "HEAD must perform the original GET-only Platform validation too");
  for (const recorded of f.wire) {
    assert.equal(recorded.method, "GET");
    assert.equal(recorded.url, path);
    assert.equal(recorded.headers.host, "platform-lab:8000");
    for (const forbidden of ["cookie", "authorization", "proxy-authorization", "x-bootstrap-key", "x-platform-bootstrap-token", "x-forwarded-host", "x-forwarded-proto", "x-original-url", "range"]) {
      assert.equal(recorded.headers[forbidden], undefined, forbidden);
    }
  }
});

test("mock reference bridge delegates tampered, expired and disabled assets to Platform without inventing success", async (t) => {
  const denial = Buffer.from('{"detail":"Input asset does not exist"}');
  const f = await inputFixture(t, (_, response) => { response.writeHead(404, { "Content-Type": "application/json", "Content-Length": String(denial.length) }); response.end(denial); });
  for (const path of [inputPath({ signature: "f".repeat(64) }), inputPath({ expires: 1 }), inputPath()]) {
    const result = await f.read("GET", path);
    assert.equal(result.status, 404);
    assert.deepEqual(result.body, denial);
  }
  assert.equal(f.requests.length, 3, "only Platform can evaluate signature, current expiry, and asset disabled state");
});

test("reference bridge rejects SSRF, alternate hosts, malformed signatures and nonexact namespaces before forwarding", async (t) => {
  const f = await inputFixture(t, () => assert.fail("invalid input target must not reach Platform"));
  const good = inputPath();
  const invalid = [
    good.replace(INPUT_PATH, INPUT_PATH + "/"), good.replace(INPUT_ID, "not-a-uuid"), good.replace("/input-assets/", "/input-assets/%2e%2e/"),
    good.replace("/content?", "%2fcontent?"), good.replace("/content?", "/content#ignored?"), good.replace("/content?", "/content??"),
    good.replace("/content?", "/content\\?"), good.replace(INPUT_PATH, "/api/v1/input-assets/../companies"),
    "http://169.254.169.254" + good, "//attacker.example" + good, good + "&url=http://169.254.169.254/latest/meta-data/",
    good + "&signature=" + "c".repeat(64), good.replace("disposition=inline", "disposition=inline&%64isposition=attachment"),
    good.replace("signature=", "Signature="), good.replace("signature=", "signature=short"), good.replace("signature=", "signature=%ZZ"),
    good.replace("signature=", "signature=%0d%0a"), good.replace("signature=", "signature=%25"), good.replace("signature=", "signature=+"),
    good.replace("expires=", "expires=-"), good.replace("expires=", "expires=1.5"), good.replace("disposition=inline", "disposition=http%3A%2F%2Fevil"),
    good.replace("&disposition=inline", ""), good.replace("&signature=", "&&signature="),
  ];
  for (const path of invalid) {
    const result = await f.read("GET", path);
    assert.ok(result.status >= 400, path);
  }
  for (const host of ["attacker.example", "platform-lab:8000", "lab-objects.local.test:443", "LAB-OBJECTS.LOCAL.TEST", "lab-objects.local.test.", f.options.bucket + "." + LAB_OBJECT_ENDPOINT]) {
    assert.equal((await f.read("GET", good, { host })).status, 400, host);
  }
  for (const method of ["POST", "PUT", "DELETE", "OPTIONS"]) assert.equal((await f.read(method, good)).status, 405, method);
  assert.equal((await f.read("GET", good, { "content-length": "1" }, "x")).status, 400);
  assert.equal(f.requests.length, 0);
  assert.throws(() => authorizeLabObjectDownloadURL(`https://${LAB_OBJECT_ENDPOINT}${good}`, f.options), "OBS signed output authorization never expands into the input-assets namespace");
});

test("live object storage never opens the mock reference bridge", async (t) => {
  const f = await inputFixture(t, () => assert.fail("live input bridge cannot call Platform"), { mode: "live" });
  for (const method of ["GET", "HEAD"]) assert.equal((await f.read(method, inputPath())).status, 404);
  assert.equal(f.requests.length, 0);
});

test("reference bridge never follows redirects or forwards redirect locations", async (t) => {
  let location = "http://169.254.169.254/latest/meta-data/";
  const f = await inputFixture(t, (_, response) => { response.writeHead(302, { location }); response.end(); });
  for (const destination of [location, "http://platform-lab:8000/api/v1/admin", "https://api.minimaxi.com/v2/video_generation"]) {
    location = destination;
    const result = await f.read("GET", inputPath());
    assert.equal(result.status, 502);
    assert.equal(result.headers.location, undefined);
    assert.match(result.body.toString(), /InputAssetRedirectRejected/);
  }
  assert.equal(f.requests.length, 3, "one fixed upstream request per supplied signed URL, never a redirect request");
});

test("reference bridge enforces 64 MiB for declared and streamed bodies without forwarding partial data", async (t) => {
  let behaviour = "declared-large";
  const chunk = Buffer.alloc(1024 * 1024, 71);
  const f = await inputFixture(t, async (_, response) => {
    response.on("error", () => {});
    if (behaviour === "declared-large") { response.writeHead(200, { "Content-Length": String(INPUT_MAX + 1) }); response.end(); return; }
    if (behaviour === "compressed") { response.writeHead(200, { "Content-Encoding": "gzip" }); response.end(BODY); return; }
    response.writeHead(200, behaviour === "exact-limit" ? { "Content-Type": "video/mp4", "Content-Length": String(INPUT_MAX) } : { "Content-Type": "video/mp4" });
    for (let i = 0; i < 64 && !response.destroyed; i++) {
      if (!response.write(chunk)) await new Promise((done) => {
        const ready = () => { response.removeListener("drain", ready); response.removeListener("close", ready); done(); };
        response.once("drain", ready); response.once("close", ready);
      });
    }
    if (!response.destroyed) response.end(behaviour === "streamed-large" ? Buffer.from([1]) : undefined);
  });
  for (const current of ["declared-large", "streamed-large"]) {
    behaviour = current;
    const result = await f.read("GET", inputPath());
    assert.equal(result.status, 413, current);
    assert.match(result.body.toString(), /InputAssetTooLarge/);
    assert.ok(result.body.length < 1024, "not one partial reference byte is returned");
  }
  behaviour = "exact-limit";
  const exact = await f.read("HEAD", inputPath());
  assert.equal(exact.status, 200);
  assert.equal(exact.headers["content-length"], String(INPUT_MAX));
  assert.equal(exact.body.length, 0);
  behaviour = "compressed";
  const compressed = await f.read("GET", inputPath());
  assert.equal(compressed.status, 502, "never transparently decode or change input bytes");
});
