import assert from "node:assert/strict";
import test from "node:test";
import { createHmac } from "node:crypto";
import { request as requestHTTP } from "node:http";
import { createLabGateway, injectLabRuntime, isolatedCookieHeader, isolatedSetCookie, labCookieName, localObjectDownloadUrl } from "../scripts/local-video-lab-gateway.mjs";

const id = "mock-0123456789ab";

test("local login supports an explicit JSON UI flow, truthful retry feedback and unchanged secure cookies", async (t) => {
  const manifest = { kind: "ai-video-local-video-lab", lab_id: id, provider_mode: "mock", instance_nonce: "a".repeat(32),
    targets: { platform_base: "http://127.0.0.1:18420", gateway_base: "http://127.0.0.1:18480", frontend_base: "http://127.0.0.1:14178" } };
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls++;
    assert.equal(url, "http://127.0.0.1:18420/internal/local-video-lab/login");
    assert.equal(init.method, "POST");
    assert.equal(init.redirect, "error");
    assert.equal(init.body, "{}");
    assert.equal(init.headers["X-Local-Video-Lab-ID"], id);
    const headers = new Headers();
    headers.append("Set-Cookie", "__Host-ai_video_session=unit-session; HttpOnly; Secure; SameSite=lax; Path=/");
    headers.append("Set-Cookie", "__Host-ai_video_csrf=unit-csrf; Secure; SameSite=lax; Path=/");
    return new Response("{}", { status: 200, headers });
  });
  const server = createLabGateway({ manifest, receipt: { lab_id: id, company_id: "01234567-0123-4012-8012-0123456789ab" }, bootstrapToken: "b".repeat(64), clientRoot: "." });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { server.closeAllConnections(); server.close(done); }));
  const request = (method, path, extra = {}) => new Promise((done, reject) => {
    const sent = requestHTTP({ hostname: "127.0.0.1", port: server.address().port, path, method,
      headers: { Host: "127.0.0.1:14178", Origin: manifest.targets.frontend_base, ...extra } }, (received) => {
      const body = []; received.on("data", (chunk) => body.push(chunk));
      received.on("end", () => done({ status: received.statusCode, headers: received.headers, body: Buffer.concat(body).toString("utf8") }));
    });
    sent.on("error", reject); sent.end();
  });
  const page = await request("GET", "/__local-video-lab__");
  assert.equal(page.status, 200);
  assert.match(page.body, /aria-live="polite"/);
  assert.match(page.body, /credentials:"same-origin"/);
  assert.match(page.body, /response.ok/);
  const login = await request("POST", "/__local-video-lab__/enter", { Accept: "application/json" });
  assert.equal(login.status, 200);
  assert.deepEqual(JSON.parse(login.body), { redirect: "/creation" });
  assert.equal(calls, 1);
  assert.equal(login.headers["set-cookie"].length, 2);
  assert.ok(login.headers["set-cookie"].every((cookie) => cookie.includes("; Secure;") && cookie.includes("SameSite=lax") && cookie.includes("Path=/")));
  assert.match(login.headers["set-cookie"][0], /^__Host-ai_video_lab_mock_0123456789ab_session=/);
  assert.match(login.headers["set-cookie"][0], /HttpOnly/);
  const rejected = await request("POST", "/__local-video-lab__/enter", { Origin: "https://another-site.invalid", Accept: "application/json" });
  assert.equal(rejected.status, 403); assert.equal(calls, 1);
});

test("video lab never forwards the user's existing studio or identity cookies", () => {
  const labName = labCookieName("__Host-ai_video_session", id);
  assert.equal(isolatedCookieHeader(`__Host-ai_video_session=real; unrelated=secret; ${labName}=isolated`, id), "__Host-ai_video_session=isolated");
  assert.equal(isolatedCookieHeader("__Host-ai_video_session=real; __Host-ai_video_csrf=real-csrf", id), "");
  assert.equal(isolatedCookieHeader(`${labCookieName("__Host-ai_video_session", "live-0123456789ab")}=other-mode`, id), "");
});

test("lab Set-Cookie preserves security attributes without overwriting real cookies", () => {
  const value = "__Host-ai_video_session=isolated; HttpOnly; Secure; SameSite=lax; Path=/";
  assert.equal(isolatedSetCookie(value, id), "__Host-ai_video_lab_mock_0123456789ab_session=isolated; HttpOnly; Secure; SameSite=lax; Path=/");
  assert.equal(isolatedSetCookie("other=unrecognized; Path=/", id), null);
  assert.throws(() => labCookieName("__Host-ai_video_session", "../../real"));
});

test("production frontend receives only the lab Platform origin and actual company identifier", () => {
  const html = injectLabRuntime("<html><head><script type=module src=/assets/app.js></script></head></html>", { frontendBase: "http://127.0.0.1:14178", companyId: "01234567-0123-4012-8012-0123456789ab" });
  assert.ok(html.indexOf("__AI_VIDEO_RUNTIME_CONFIG__") < html.indexOf("type=module"));
  assert.ok(html.includes('"platformApiUrl":"http://127.0.0.1:14178"'));
  assert.ok(!/key|token|userId|demoMode|authorization/i.test(html));
  assert.throws(() => injectLabRuntime("<head>", { frontendBase: "http://127.0.0.1:8180", companyId: "01234567-0123-4012-8012-0123456789ab" }));
});

test("gateway rejects an ordinary stack or a receipt belonging to a different lab", () => {
  const manifest = { lab_id: id, targets: { platform_base: "http://127.0.0.1:8200", gateway_base: "http://127.0.0.1:18480", frontend_base: "http://127.0.0.1:14178" } };
  assert.throws(() => createLabGateway({ manifest, receipt: { lab_id: id }, bootstrapToken: "a".repeat(40), clientRoot: "." }));
  manifest.targets.platform_base = "http://127.0.0.1:18420";
  assert.throws(() => createLabGateway({ manifest, receipt: { lab_id: "live-0123456789ab" }, bootstrapToken: "a".repeat(40), clientRoot: "." }));
});

test("only the exact current local signed object can become a same-origin preview", () => {
  const object = { environment: "development", mode: "mock", stateId: "mock-" + "a".repeat(64), endpointHost: "lab-objects.local.test", bucket: "video-mock-" + "a".repeat(20), accessKeyId: "LAB" + "B".repeat(20), secretAccessKey: "c".repeat(64) };
  const uuid = "01234567-0123-4012-8012-0123456789ab";
  const path = "/outputs/" + [uuid, uuid, uuid].join("/");
  const expiry = String(Math.floor(Date.now() / 1000) + 300);
  const signature = createHmac("sha1", object.secretAccessKey).update("GET\n\n\n" + expiry + "\n/" + object.bucket + path).digest("base64");
  const params = new URLSearchParams({ AccessKeyId: object.accessKeyId, Expires: expiry, Signature: signature });
  const value = "https://" + object.bucket + ".lab-objects.local.test" + path + "?" + params;
  assert.equal(localObjectDownloadUrl(value, object), "http://127.0.0.1:14178/__local-video-lab__/objects/" + object.bucket + path + "?" + params);
  assert.equal(localObjectDownloadUrl(value.replace("AccessKeyId=", "AWSAccessKeyId="), object),
    "http://127.0.0.1:14178/__local-video-lab__/objects/" + object.bucket + path + "?" + params.toString().replace("AccessKeyId=", "AWSAccessKeyId="));
  for (const invalid of [
    value.replace("https:", "http:"), value.replace("lab-objects.local.test", "attacker.invalid"),
    value.replace(".test/", ".test:443/"), value.replace("/outputs/", "/outputs/../"),
    value.replace(object.accessKeyId, "WRONGKEY"), value.replace(/Signature=[^&]+/, "Signature=" + encodeURIComponent("a".repeat(28))), value + "&redirect=https://attacker.invalid", value + "&Signature=extra",
    value + "#hidden", value.replace(/Expires=\d+/, "Expires=1000000000"),
    value.replace(uuid, "%2e%2e"),
  ]) assert.throws(() => localObjectDownloadUrl(invalid, object));
  assert.throws(() => localObjectDownloadUrl(value, { ...object, bucket: "video-live-" + "a".repeat(20) }));
});
