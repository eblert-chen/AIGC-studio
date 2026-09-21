#!/usr/bin/env node
// Development-only persistent OBS-v2 wire substitute. Not Huawei production
// storage or evidence of an OBS account. The real Relay store still performs
// Put, HEAD verification, immutable-key cleanup, and signed binding issuance.
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readFile, readdir, realpath, unlink } from "node:fs/promises";
import { request as requestHTTP } from "node:http";
import { createServer } from "node:https";
import { isAbsolute, join, parse, resolve } from "node:path";
import { pipeline } from "node:stream/promises";
import { pathToFileURL } from "node:url";

export const LAB_OBJECT_ENDPOINT = "lab-objects.local.test";
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const OBJECT_KEY = new RegExp(`^outputs/${UUID}/${UUID}/${UUID}$`);
const INPUT_ASSET_PATH = new RegExp(`^/api/v1/input-assets/${UUID}/content\\?([^#\\s]+)$`);
const MAX_BYTES = 64 * 1024 * 1024;
const noFollow = constants.O_NOFOLLOW || 0;

class StoreError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}

function fail(status, code) { throw new StoreError(status, code); }
function digest(value) { return createHash("sha256").update(value).digest("hex"); }
function same(left, right) {
  const a = Buffer.from(String(left)), b = Buffer.from(String(right));
  return a.length === b.length && timingSafeEqual(a, b);
}

function validateIdentity(options) {
  if (options.environment !== "development" || !["mock", "live"].includes(options.mode)
      || !new RegExp(`^${options.mode}-[a-f0-9]{64}$`).test(options.stateId)
      || options.endpointHost !== LAB_OBJECT_ENDPOINT
      || !new RegExp(`^video-${options.mode}-[a-f0-9]{20}$`).test(options.bucket)
      || !/^[A-Za-z0-9._-]{16,128}$/.test(options.accessKeyId)
      || !/^[A-Za-z0-9._-]{32,128}$/.test(options.secretAccessKey)) {
    throw new Error("Object storage requires exact isolated development bindings");
  }
  return options;
}

function parseTarget(raw, host, { endpointHost, bucket }) {
  if (![endpointHost, `${endpointHost}:443`, `${bucket}.${endpointHost}`, `${bucket}.${endpointHost}:443`].includes(host)
      || typeof raw !== "string" || !/^\/(?!\/)/.test(raw) || /[\\\r\n#]/.test(raw) || raw.length > 4096) fail(400, "InvalidTarget");
  const [rawPath] = raw.split("?", 1);
  if (rawPath.includes("%") || rawPath.includes("//") || rawPath.split("/").some((part) => part === "." || part === "..")) fail(400, "InvalidTarget");
  const url = new URL(raw, `https://${host}`);
  const virtual = host === `${bucket}.${endpointHost}` || host === `${bucket}.${endpointHost}:443`;
  const prefix = virtual ? "/" : `/${bucket}/`;
  let key;
  if (!virtual && url.pathname === `/${bucket}`) key = "";
  else if (url.pathname.startsWith(prefix)) key = url.pathname.slice(prefix.length);
  else fail(404, "NoSuchBucket");
  if (key && !OBJECT_KEY.test(key)) fail(400, "InvalidObjectKey");
  const query = new Map();
  for (const [name, value] of url.searchParams) {
    if (query.has(name)) fail(400, "DuplicateQueryParameter");
    query.set(name, value);
  }
  return { url, key, query, resource: `/${bucket}/${key}` };
}

function canonicalHeaders(headers, prefix, date) {
  const selected = new Map([
    ["content-md5", String(headers["content-md5"] || "")],
    ["content-type", String(headers["content-type"] || "")],
    ["date", date ?? String(headers.date || "")],
  ]);
  for (const [name, value] of Object.entries(headers)) {
    const lower = name.toLowerCase();
    if (!lower.startsWith(prefix)) continue;
    const values = Array.isArray(value) ? value : [String(value)];
    selected.set(lower, values.map((item) => lower.startsWith(`${prefix}meta-`) ? item.trim() : item).join(","));
  }
  if (date === undefined && selected.has(`${prefix}date`)) selected.set("date", "");
  return [...selected].sort(([a], [b]) => a.localeCompare(b, "en")).map(([name, value]) => name.startsWith(prefix) ? `${name}:${value}` : value).join("\n");
}

function signature(method, resource, headers, prefix, key, date) {
  return createHmac("sha1", key).update(`${method}\n${canonicalHeaders(headers, prefix, date)}\n${resource}`).digest("base64");
}

function authorize(target, request, options) {
  const seconds = Math.floor((options.clock?.() ?? Date.now()) / 1000);
  const { query } = target;
  if (query.has("Signature")) {
    const accessParameter = query.has("AccessKeyId") ? "AccessKeyId" : "AWSAccessKeyId";
    if (query.size !== 3 || !query.has(accessParameter) || !query.has("Expires") || !["GET", "HEAD"].includes(request.method)
        || request.headers.authorization || !target.key) fail(403, "AccessDenied");
    const expiry = query.get("Expires");
    if (!/^[1-9][0-9]{0,11}$/.test(expiry) || Number(expiry) <= seconds || Number(expiry) > seconds + 3600
        || !same(query.get(accessParameter), options.accessKeyId)) fail(403, "ExpiredOrInvalidSignature");
    // A GET bearer URL permits HEAD of that exact object as a strictly narrower
    // read. Range never changes which immutable bytes the signature authorizes.
    const expected = signature("GET", target.resource, {}, accessParameter === "AccessKeyId" ? "x-obs-" : "x-amz-", options.secretAccessKey, expiry);
    if (!same(query.get("Signature"), expected)) fail(403, "SignatureDoesNotMatch");
    return { signed: true, expires: Number(expiry) };
  }
  if (query.size > 0 && !(query.size === 1 && query.has("versioning") && query.get("versioning") === "" && !target.key)) fail(400, "UnsupportedOperation");
  const auth = /^(OBS|AWS) ([A-Za-z0-9._-]{16,128}):([A-Za-z0-9+/=]{20,128})$/.exec(request.headers.authorization || "");
  if (!auth || !same(auth[2], options.accessKeyId)) fail(403, "AccessDenied");
  const prefix = auth[1] === "OBS" ? "x-obs-" : "x-amz-";
  const date = Date.parse(request.headers[`${prefix}date`] || request.headers.date || "");
  if (!Number.isFinite(date) || Math.abs(seconds - Math.floor(date / 1000)) > 300) fail(403, "RequestTimeTooSkewed");
  const resource = target.resource + (query.has("versioning") ? "?versioning" : "");
  if (!same(auth[3], signature(request.method, resource, request.headers, prefix, options.secretAccessKey))) fail(403, "SignatureDoesNotMatch");
  return { signed: false, prefix };
}

// The host gateway may use this to map ONLY a Platform-issued, already strictly
// bound URL onto a same-origin media path. It returns no secrets and never
// permits a caller-selected host, bucket, path namespace, method or query field.
export function authorizeLabObjectDownloadURL(raw, options) {
  validateIdentity(options);
  let url;
  try { url = new URL(raw); } catch { fail(400, "InvalidTarget"); }
  if (url.protocol !== "https:" || url.username || url.password || url.hash || (url.port && url.port !== "443") || url.href !== raw) fail(400, "InvalidTarget");
  const target = parseTarget(url.pathname + url.search, url.host, options);
  const authorization = authorize(target, { method: "GET", headers: {} }, options);
  if (!authorization.signed) fail(403, "AccessDenied");
  return { hostname: url.hostname, path: url.pathname + url.search, objectKey: target.key, expiresAt: authorization.expires };
}

function validateInputAssetBridgeRequest(request, options) {
  if (options.mode !== "mock") fail(404, "InputAssetBridgeUnavailable");
  if (!["GET", "HEAD"].includes(request.method)) fail(405, "UnsupportedOperation");
  if (request.headers.host !== LAB_OBJECT_ENDPOINT || typeof request.url !== "string" || request.url.length > 2048
      || /[\\\x00-\x20\x7f]/.test(request.url) || request.headers["transfer-encoding"]
      || (request.headers["content-length"] && request.headers["content-length"] !== "0")) fail(400, "InvalidInputAssetTarget");
  const match = INPUT_ASSET_PATH.exec(request.url);
  if (!match) fail(400, "InvalidInputAssetTarget");
  const fields = new Map();
  const parts = match[1].split("&");
  if (parts.length !== 3) fail(400, "InvalidInputAssetQuery");
  for (const part of parts) {
    const pair = /^([^=]+)=([^=]+)$/.exec(part);
    if (!pair) fail(400, "InvalidInputAssetQuery");
    let name, value;
    try { name = decodeURIComponent(pair[1].replaceAll("+", " ")); value = decodeURIComponent(pair[2].replaceAll("+", " ")); }
    catch { fail(400, "InvalidInputAssetQuery"); }
    if (!["expires", "disposition", "signature"].includes(name) || fields.has(name)) fail(400, "InvalidInputAssetQuery");
    fields.set(name, value);
  }
  if (!/^[1-9][0-9]{0,11}$/.test(fields.get("expires") || "") || !["inline", "attachment"].includes(fields.get("disposition"))
      || !/^[0-9a-f]{64}$/.test(fields.get("signature") || "")) fail(400, "InvalidInputAssetQuery");
  // Do not verify or reissue signatures here. Platform owns their expiry,
  // asset state, and content authorization. Never reserialize the raw query.
}

async function forwardInputAsset(request, response, requestUpstream) {
  let outbound, upstream, timedOut = false;
  const cancel = () => outbound?.destroy();
  const timer = setTimeout(() => { timedOut = true; outbound?.destroy(); }, 15_000);
  timer.unref();
  request.once("aborted", cancel); response.once("close", cancel);
  try {
    upstream = await new Promise((done, reject) => {
      // Deliberately no configurable URL, caller headers, proxy, or redirect
      // client. HEAD runs Platform's actual GET-only signed-content endpoint.
      outbound = requestUpstream({ protocol: "http:", hostname: "platform-lab", port: 8000, method: "GET", path: request.url,
        agent: false, maxHeaderSize: 8192, headers: { host: "platform-lab:8000", accept: "*/*", "accept-encoding": "identity" } }, done);
      outbound.once("error", () => reject(new StoreError(502, "InputAssetUpstreamUnavailable")));
      outbound.end();
    });
    const status = upstream.statusCode;
    if (status >= 300 && status < 400) fail(502, "InputAssetRedirectRejected");
    if (status !== 200 && !(status >= 400 && status <= 599)) fail(502, "InvalidInputAssetResponse");
    if (upstream.headers["content-encoding"] && upstream.headers["content-encoding"] !== "identity") fail(502, "InvalidInputAssetEncoding");
    const length = upstream.headers["content-length"];
    if (length !== undefined && (typeof length !== "string" || !/^(0|[1-9][0-9]*)$/.test(length))) fail(502, "InvalidInputAssetLength");
    const declared = length === undefined ? null : Number(length);
    if (declared !== null && (!Number.isSafeInteger(declared) || declared > MAX_BYTES)) fail(413, "InputAssetTooLarge");
    const chunks = [];
    const fixed = declared === null ? null : Buffer.allocUnsafe(declared);
    let received = 0;
    for await (const chunk of upstream) {
      if (received + chunk.length > MAX_BYTES) fail(413, "InputAssetTooLarge");
      if (declared !== null && received + chunk.length > declared) fail(502, "InvalidInputAssetLength");
      if (fixed) chunk.copy(fixed, received);
      else chunks.push(chunk);
      received += chunk.length;
    }
    if (declared !== null && received !== declared) fail(502, "InvalidInputAssetLength");
    if (status === 200 && received === 0) fail(502, "EmptyInputAsset");
    const body = fixed || Buffer.concat(chunks, received);
    for (const name of ["content-type", "content-disposition", "etag", "last-modified"]) {
      if (typeof upstream.headers[name] === "string") response.setHeader(name, upstream.headers[name]);
    }
    response.statusCode = status;
    response.setHeader("Content-Length", String(body.length));
    response.end(request.method === "HEAD" ? undefined : body);
  } catch (error) {
    if (timedOut) fail(504, "InputAssetUpstreamTimedOut");
    if (error instanceof StoreError) throw error;
    fail(502, "InputAssetUpstreamUnavailable");
  } finally {
    clearTimeout(timer);
    request.removeListener("aborted", cancel); response.removeListener("close", cancel);
    upstream?.destroy(); outbound?.destroy();
  }
}

async function readRegular(path, maxBytes) {
  const file = await open(path, constants.O_RDONLY | noFollow);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size < 1 || info.size > maxBytes) fail(500, "InvalidStoredObject");
    return await file.readFile();
  } finally { await file.close(); }
}

async function createExclusiveJSON(path, value) {
  const file = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | noFollow, 0o600);
  try { await file.writeFile(JSON.stringify(value) + "\n"); await file.sync(); }
  finally { await file.close(); }
}

async function initializeRoot(options) {
  if (!isAbsolute(options.root) || resolve(options.root) === parse(resolve(options.root)).root) throw new Error("Object storage root must be a dedicated absolute directory");
  const root = resolve(options.root);
  await mkdir(root, { recursive: true, mode: 0o700 });
  const info = await lstat(root);
  if (!info.isDirectory() || info.isSymbolicLink() || resolve(await realpath(root)) !== root) throw new Error("Object storage cannot traverse symlinks");
  const identity = { schema_version: 1, kind: "local-persistent-object-store-not-production-obs", mode: options.mode, state_id: options.stateId,
    endpoint_host: options.endpointHost, bucket: options.bucket, credential_sha256: digest(`${options.accessKeyId}\0${options.secretAccessKey}`) };
  const identityPath = join(root, ".lab-object-store.json");
  const entries = await readdir(root);
  if (entries.length === 0) await createExclusiveJSON(identityPath, identity);
  else {
    let previous;
    try { previous = JSON.parse(await readRegular(identityPath, 4096)); } catch { throw new Error("Refusing unmarked or invalid local object storage state"); }
    if (JSON.stringify(previous) !== JSON.stringify(identity)) throw new Error("Object storage identity changed; a fresh independent data volume is required");
  }
  const objects = join(root, "objects");
  await mkdir(objects, { recursive: true, mode: 0o700 });
  const objectInfo = await lstat(objects);
  if (!objectInfo.isDirectory() || objectInfo.isSymbolicLink()) throw new Error("Object storage directory is unsafe");
  return { root, objects, identity };
}

function objectPaths(directory, key) {
  const id = digest(key);
  return { content: join(directory, id + ".blob"), metadata: join(directory, id + ".json") };
}

async function verifyStored(directory, key, options) {
  const paths = objectPaths(directory, key);
  let metadata, file;
  try {
    metadata = JSON.parse(await readRegular(paths.metadata, 4096));
    if (Object.keys(metadata).sort().join(",") !== "content_type,md5,object_key,schema_version,sha256,size_bytes,state_id"
        || metadata.schema_version !== 1 || metadata.state_id !== options.stateId || metadata.object_key !== key
        || metadata.content_type !== "video/mp4" || !Number.isSafeInteger(metadata.size_bytes) || metadata.size_bytes < 1 || metadata.size_bytes > options.maxBytes
        || !/^[a-f0-9]{64}$/.test(metadata.sha256) || !/^[a-f0-9]{32}$/.test(metadata.md5)) fail(500, "InvalidStoredObject");
    file = await open(paths.content, constants.O_RDONLY | noFollow);
    const info = await file.stat();
    if (!info.isFile() || info.size !== metadata.size_bytes) fail(500, "ObjectIntegrityMismatch");
    const sha = createHash("sha256"), md5 = createHash("md5");
    for await (const chunk of file.createReadStream({ autoClose: false, start: 0 })) { sha.update(chunk); md5.update(chunk); }
    if (sha.digest("hex") !== metadata.sha256 || md5.digest("hex") !== metadata.md5) fail(500, "ObjectIntegrityMismatch");
    return { metadata, file };
  } catch (error) {
    await file?.close().catch(() => {});
    if (error.code === "ENOENT") fail(404, "NoSuchKey");
    if (error instanceof StoreError) throw error;
    fail(500, "InvalidStoredObject");
  }
}

async function putObject(request, directory, key, options, prefix) {
  const length = request.headers["content-length"];
  const declared = request.headers[`${prefix}meta-size-bytes`];
  const expectedSHA = request.headers[`${prefix}meta-sha256`];
  if (!/^[1-9][0-9]*$/.test(length || "") || Number(length) > options.maxBytes || declared !== length
      || !/^[a-f0-9]{64}$/.test(expectedSHA || "") || request.headers["content-type"] !== "video/mp4") fail(400, "InvalidObjectMetadata");
  const temporary = join(directory, ".upload-" + randomUUID());
  const file = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | noFollow, 0o600);
  let received = 0;
  const sha = createHash("sha256"), md5 = createHash("md5");
  try {
    for await (const chunk of request) {
      received += chunk.length;
      if (received > Number(length) || received > options.maxBytes) fail(413, "ObjectTooLarge");
      sha.update(chunk); md5.update(chunk);
      let offset = 0;
      while (offset < chunk.length) {
        const result = await file.write(chunk, offset, chunk.length - offset);
        if (result.bytesWritten < 1) fail(500, "ObjectWriteFailed");
        offset += result.bytesWritten;
      }
    }
    const actualSHA = sha.digest("hex"), actualMD5 = md5.digest("hex");
    if (received !== Number(length) || actualSHA !== expectedSHA
        || (request.headers["content-md5"] && request.headers["content-md5"] !== Buffer.from(actualMD5, "hex").toString("base64"))) fail(400, "ObjectIntegrityMismatch");
    await file.sync(); await file.close();
    const metadata = { schema_version: 1, state_id: options.stateId, object_key: key, content_type: "video/mp4", size_bytes: received, sha256: actualSHA, md5: actualMD5 };
    const paths = objectPaths(directory, key);
    try { await link(temporary, paths.content); }
    catch (error) {
      if (error.code !== "EEXIST") throw error;
      const existing = await verifyStored(directory, key, options);
      await existing.file.close();
      if (JSON.stringify(existing.metadata) !== JSON.stringify(metadata)) fail(409, "ImmutableObjectConflict");
    }
    try { await createExclusiveJSON(paths.metadata, metadata); }
    catch (error) {
      if (error.code !== "EEXIST") throw error;
      const existing = await verifyStored(directory, key, options);
      await existing.file.close();
      if (JSON.stringify(existing.metadata) !== JSON.stringify(metadata)) fail(409, "ImmutableObjectConflict");
    }
    return metadata;
  } finally { await file.close().catch(() => {}); await unlink(temporary).catch(() => {}); }
}

function parseRange(value, size) {
  if (!value) return { start: 0, end: size - 1, partial: false };
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2])) fail(416, "InvalidRange");
  let start, end;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix < 1) fail(416, "InvalidRange");
    start = Math.max(0, size - suffix); end = size - 1;
  } else {
    start = Number(match[1]); end = match[2] ? Number(match[2]) : size - 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= size || end < start) fail(416, "InvalidRange");
    end = Math.min(end, size - 1);
  }
  return { start, end, partial: true };
}

function outputHeaders(response, metadata) {
  response.setHeader("Content-Type", metadata.content_type);
  response.setHeader("ETag", `"${metadata.md5}"`);
  response.setHeader("Accept-Ranges", "bytes");
  response.setHeader("Content-Disposition", 'inline; filename="artifact.mp4"');
  for (const prefix of ["x-obs-meta-", "x-amz-meta-"]) {
    response.setHeader(prefix + "sha256", metadata.sha256);
    response.setHeader(prefix + "size-bytes", String(metadata.size_bytes));
  }
}

/** Injectable request handler for offline protocol tests. CLI always uses TLS.
 * The optional request transport is a test seam, never an environment option;
 * its protocol, destination, headers, and request target remain fixed here. */
export async function createLabObjectStoreHandler(input, { inputAssetRequest = requestHTTP } = {}) {
  const options = { ...validateIdentity(input), maxBytes: input.maxBytes ?? MAX_BYTES };
  if (!Number.isSafeInteger(options.maxBytes) || options.maxBytes < 1 || options.maxBytes > MAX_BYTES) throw new Error("Object storage size limit is invalid");
  const storage = await initializeRoot(options);
  const locks = new Map();
  const withLock = async (key, action) => {
    const previous = locks.get(key) || Promise.resolve();
    let release;
    const locked = new Promise((done) => { release = done; });
    locks.set(key, locked);
    await previous;
    try { return await action(); }
    finally { release(); if (locks.get(key) === locked) locks.delete(key); }
  };
  return async (request, response) => {
    response.setHeader("Cache-Control", "private, no-store, max-age=0");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader("Referrer-Policy", "no-referrer");
    response.setHeader("x-obs-request-id", randomUUID());
    response.setHeader("x-amz-request-id", randomUUID());
    try {
      if (request.url === "/health" && [options.endpointHost, `${options.endpointHost}:443`].includes(request.headers.host) && request.method === "GET") {
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({ kind: storage.identity.kind, state_id: options.stateId, mode: options.mode, protocol: "obs-v2", production_ready: false }));
        return;
      }
      if (typeof request.url === "string" && request.url.startsWith("/api/v1/input-assets/")) {
        validateInputAssetBridgeRequest(request, options);
        await forwardInputAsset(request, response, inputAssetRequest);
        return;
      }
      const target = parseTarget(request.url, request.headers.host, options);
      const auth = authorize(target, request, options);
      if (!target.key) {
        if (request.method === "HEAD" && target.query.size === 0) { response.writeHead(200, { "Content-Length": "0" }); response.end(); return; }
        if (request.method === "GET" && target.query.has("versioning")) { response.writeHead(200, { "Content-Type": "application/xml" }); response.end('<?xml version="1.0" encoding="UTF-8"?><VersioningConfiguration/>'); return; }
        fail(405, "UnsupportedOperation");
      }
      if (request.method === "PUT" && !auth.signed) {
        const metadata = await withLock(target.key, () => putObject(request, storage.objects, target.key, options, auth.prefix));
        outputHeaders(response, metadata); response.writeHead(200, { "Content-Length": "0" }); response.end(); return;
      }
      if (request.method === "DELETE" && !auth.signed) {
        await withLock(target.key, async () => {
          for (const path of Object.values(objectPaths(storage.objects, target.key)).reverse()) {
            try { await unlink(path); } catch (error) { if (error.code !== "ENOENT") throw error; }
          }
        });
        response.writeHead(204); response.end(); return;
      }
      if (!["GET", "HEAD"].includes(request.method)) fail(405, "UnsupportedOperation");
      const { metadata, file } = await verifyStored(storage.objects, target.key, options);
      try {
        outputHeaders(response, metadata);
        response.setHeader("Content-Range", `bytes */${metadata.size_bytes}`);
        const range = parseRange(!request.headers["if-range"] || request.headers["if-range"] === `"${metadata.md5}"` ? request.headers.range : "", metadata.size_bytes);
        if (range.partial) response.setHeader("Content-Range", `bytes ${range.start}-${range.end}/${metadata.size_bytes}`);
        else response.removeHeader("Content-Range");
        response.setHeader("Content-Length", String(range.end - range.start + 1));
        response.statusCode = range.partial ? 206 : 200;
        if (request.method === "HEAD") { response.end(); return; }
        await pipeline(file.createReadStream({ autoClose: false, start: range.start, end: range.end }), response);
      } finally { await file.close(); }
    } catch (error) {
      if (response.headersSent || response.destroyed) { response.destroy(); return; }
      const status = error instanceof StoreError ? error.status : 500;
      const code = error instanceof StoreError ? error.code : "LocalObjectStoreError";
      response.removeHeader("Content-Length");
      response.writeHead(status, { "Content-Type": "application/xml" });
      response.end(`<?xml version="1.0" encoding="UTF-8"?><Error><Code>${code}</Code><Message>Local isolated object request rejected</Message></Error>`);
    }
  };
}

export function objectStoreOptionsFromEnvironment(environment) {
  for (const key of ["APP_ENV", "ENVIRONMENT", "DEPLOYMENT_ENV", "RELAY_COMPAT_ENVIRONMENT"]) {
    if (environment[key] && environment[key] !== "development") throw new Error("Object storage refuses a non-development process");
  }
  const options = validateIdentity({ environment: environment.LAB_OBJECT_STORE_ENVIRONMENT,
    mode: environment.LAB_OBJECT_STORE_MODE, stateId: environment.LAB_OBJECT_STORE_STATE_ID,
    root: environment.LAB_OBJECT_STORE_ROOT, endpointHost: environment.LAB_OBJECT_STORE_ENDPOINT_HOST,
    bucket: environment.LAB_OBJECT_STORE_BUCKET, accessKeyId: environment.LAB_OBJECT_STORE_ACCESS_KEY_ID,
    secretAccessKey: environment.LAB_OBJECT_STORE_SECRET_ACCESS_KEY });
  const port = environment.LAB_OBJECT_STORE_LISTEN_PORT || "443", host = environment.LAB_OBJECT_STORE_LISTEN_HOST || "0.0.0.0";
  if (port !== "443" || !["0.0.0.0", "127.0.0.1"].includes(host)) throw new Error("Object storage listener must use its isolated HTTPS port 443");
  return { ...options, port: Number(port), host, certFile: environment.LAB_OBJECT_STORE_TLS_CERT_FILE, keyFile: environment.LAB_OBJECT_STORE_TLS_KEY_FILE };
}

async function main() {
  const options = objectStoreOptionsFromEnvironment(process.env);
  if (!isAbsolute(options.certFile || "") || !isAbsolute(options.keyFile || "")) throw new Error("Object storage TLS files must be absolute private paths");
  const handler = await createLabObjectStoreHandler(options);
  const server = createServer({ cert: await readRegular(options.certFile, 64 * 1024), key: await readRegular(options.keyFile, 64 * 1024), minVersion: "TLSv1.2" }, handler);
  server.requestTimeout = 90_000; server.headersTimeout = 10_000; server.keepAliveTimeout = 5_000; server.maxConnections = 32;
  await new Promise((done, reject) => { server.once("error", reject); server.listen(options.port, options.host, done); });
  process.stdout.write(JSON.stringify({ kind: "local-persistent-object-store-not-production-obs", state_id: options.stateId, mode: options.mode, production_ready: false }) + "\n");
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => { server.close(); server.closeIdleConnections(); });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => { process.stderr.write("Local object storage failed; check isolated state, TLS and configuration. No credentials were printed.\n"); process.exitCode = 1; });
}
