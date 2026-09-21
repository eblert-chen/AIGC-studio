import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, resolve, sep } from "node:path";
import { authorizeLabObjectDownloadURL } from "./local-video-lab-object-store.mjs";

const platformCookies = ["__Host-ai_video_session", "__Host-ai_video_csrf", "__Host-ai_video_oidc_state", "__Secure-ai_video_invitation"];
const mediaTypes = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".woff2": "font/woff2", ".mp4": "video/mp4", ".ico": "image/x-icon" };
export function localObjectDownloadUrl(value, objectStore, frontendBase = "http://127.0.0.1:14178") {
  if (typeof value !== "string" || !objectStore || !value.startsWith("https://" + objectStore.bucket + ".lab-objects.local.test/")) throw new Error("Artifact URL must use the exact local HTTPS origin");
  if (frontendBase !== "http://127.0.0.1:14178") throw new Error("Wrong local frontend origin");
  const authorized = authorizeLabObjectDownloadURL(value, objectStore);
  return frontendBase + "/__local-video-lab__/objects/" + objectStore.bucket + authorized.path;
}

export function labCookieName(name, labId) {
  if (!/^(mock|live)-[0-9a-f]{12}$/.test(labId) || !platformCookies.includes(name)) throw new Error("Invalid isolated lab cookie identity");
  return name.replace("ai_video_", `ai_video_lab_${labId.replaceAll("-", "_")}_`);
}

export function isolatedCookieHeader(header = "", labId) {
  const mapping = new Map(platformCookies.map((name) => [labCookieName(name, labId), name]));
  return String(header).split(";").map((part) => part.trim()).flatMap((part) => {
    const index = part.indexOf("=");
    const original = mapping.get(part.slice(0, index));
    return index > 0 && original ? [`${original}=${part.slice(index + 1)}`] : [];
  }).join("; ");
}

export function isolatedSetCookie(header, labId) {
  const index = String(header).indexOf("=");
  const name = String(header).slice(0, index);
  return index > 0 && platformCookies.includes(name)
    ? labCookieName(name, labId) + String(header).slice(index)
    : null;
}

export function injectLabRuntime(html, { frontendBase, companyId }) {
  if (frontendBase !== "http://127.0.0.1:14178" || !/^[0-9a-f-]{36}$/.test(companyId)) throw new Error("Invalid lab frontend binding");
  const value = JSON.stringify({ platformApiUrl: frontendBase, companyId }).replaceAll("<", "\\u003c");
  return html.replace("<head>", `<head><script>window.__AI_VIDEO_RUNTIME_CONFIG__=${value};</script>`);
}

function respond(response, status, content, type = "application/json; charset=utf-8") {
  response.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" });
  response.end(typeof content === "string" ? content : JSON.stringify(content));
}

function landingPage(mode) {
  const description = mode === "mock"
    ? "上游为本地模拟供应商。模型参数、任务、报价和成片入库由真实服务处理；测试积分不代表现金，样片不代表模型质量。"
    : "上游为真实供应商。此处仍是独立测试工作区；点击生成会产生供应商费用，测试积分不是正式售价。";
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>本地视频联调 · 旭天</title><style>body{margin:0;background:#fafaf8;color:#14252a;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}main{max-width:580px;margin:12vh auto;padding:32px}img{width:156px;height:auto}h1{font-size:30px;letter-spacing:-.02em;margin:36px 0 12px}p{color:#53656b;margin:0 0 28px}button{background:#087b80;color:white;border:0;border-radius:10px;padding:13px 24px;font:inherit;font-weight:600;cursor:pointer}button:hover{background:#07696d}button:focus-visible{outline:3px solid #087b80;outline-offset:4px}small{display:block;margin-top:24px;color:#53656b}::selection{background:#cce9e6}</style></head><body><main><img src="/brand/xutian-ai-studio-wordmark.svg" alt="旭天 AI studio"><h1>本地视频联调</h1><p>${description}</p><form method="post" action="/__local-video-lab__/enter"><button type="submit">进入测试创作工作区</button></form><p id="entry-feedback" role="status" aria-live="polite"></p><small>独立账号与数据，不会切换或覆盖你已登录的正式工作区。</small></main><script>document.querySelector("form").addEventListener("submit",async(event)=>{event.preventDefault();const button=event.currentTarget.querySelector("button");const feedback=document.getElementById("entry-feedback");button.disabled=true;feedback.textContent="正在进入测试工作区…";try{const response=await fetch("/__local-video-lab__/enter",{method:"POST",credentials:"same-origin",redirect:"error",headers:{"Accept":"application/json","Content-Type":"application/json"},body:"{}"});const result=await response.json();if(!response.ok||result.redirect!=="/creation")throw new Error("entry");window.location.assign("/creation");}catch{feedback.textContent="暂时无法进入测试工作区。请确认联调服务运行正常后重试。";button.disabled=false;}});</script></body></html>`;
}

/** Loopback-only gateway. It never forwards real-app cookies or accepts a target URL. */
export function createLabGateway({ manifest, receipt, bootstrapToken, clientRoot, onShutdown, objectStore }) {
  const labId = manifest.lab_id;
  const frontendBase = manifest.targets.frontend_base;
  if (!/^(mock|live)-[0-9a-f]{12}$/.test(labId)
      || manifest.targets.platform_base !== "http://127.0.0.1:18420"
      || manifest.targets.gateway_base !== "http://127.0.0.1:18480"
      || frontendBase !== "http://127.0.0.1:14178"
      || receipt.lab_id !== labId || !bootstrapToken || bootstrapToken.length < 32) {
    throw new Error("Gateway requires an exact isolated local-video-lab binding");
  }
  const root = resolve(clientRoot);
  const allowedHosts = new Set(["127.0.0.1:14178", "127.0.0.1:18480"]);
  const allowedOrigins = new Set([frontendBase, manifest.targets.gateway_base]);

  return createServer(async (request, response) => {
    try {
      if (!allowedHosts.has(request.headers.host) || !["127.0.0.1", "::ffff:127.0.0.1", "::1"].includes(request.socket.remoteAddress)) {
        respond(response, 403, { error: "Local lab host binding required" }); return;
      }
      if (request.headers.origin && !allowedOrigins.has(request.headers.origin)) {
        respond(response, 403, { error: "Cross-origin lab request rejected" }); return;
      }
      if (!/^\/(?!\/)/.test(request.url) || /[\r\n\\]/.test(request.url)) {
        respond(response, 400, { error: "Only local origin-relative paths are accepted" }); return;
      }
      const url = new URL(request.url, frontendBase);
      if (url.pathname === "/__local-video-lab__/identity") {
        respond(response, 200, { kind: manifest.kind, lab_id: labId, provider_mode: manifest.provider_mode, frontend_base: frontendBase }); return;
      }
      if (url.pathname === "/__local-video-lab__/shutdown") {
        if (request.method !== "POST" || request.headers["x-local-video-lab-control"] !== bootstrapToken || !onShutdown) {
          respond(response, 403, { error: "Local operator credential required" }); return;
        }
        respond(response, 200, { stopped: labId });
        setImmediate(onShutdown); return;
      }
      if (url.pathname === "/__local-video-lab__" || url.pathname === "/__local-video-lab__/") {
        respond(response, 200, landingPage(manifest.provider_mode), "text/html; charset=utf-8"); return;
      }
      if (url.pathname === "/__local-video-lab__/enter") {
        if (request.method !== "POST" || !allowedOrigins.has(request.headers.origin)) {
          respond(response, 403, { error: "Open the local lab entry before signing in" }); return;
        }
        const login = await fetch(`${manifest.targets.platform_base}/internal/local-video-lab/login`, {
          method: "POST", redirect: "error", signal: AbortSignal.timeout(10_000),
          headers: { "X-Bootstrap-Token": bootstrapToken, "X-Local-Video-Lab-ID": labId, "X-Local-Video-Lab-Nonce": manifest.instance_nonce, "Content-Type": "application/json" },
          body: "{}",
        });
        if (!login.ok) { respond(response, 503, { error: "测试账号尚未就绪，请检查本地联调启动结果。" }); return; }
        const cookies = login.headers.getSetCookie().map((item) => isolatedSetCookie(item, labId)).filter(Boolean);
        if (!cookies.some((item) => item.startsWith(labCookieName(platformCookies[0], labId) + "="))) {
          respond(response, 503, { error: "测试会话未建立，没有切换账号。" }); return;
        }
        if (request.headers.accept?.includes("application/json")) {
          response.setHeader("Set-Cookie", cookies);
          respond(response, 200, { redirect: "/creation" }); return;
        }
        response.writeHead(303, { Location: "/creation", "Set-Cookie": cookies, "Cache-Control": "no-store" }); response.end(); return;
      }
      if (url.pathname.startsWith("/__local-video-lab__/objects/")) {
        if (!["GET", "HEAD"].includes(request.method) || !objectStore?.ca || !isolatedCookieHeader(request.headers.cookie, labId)) {
          respond(response, 403, { error: "A local lab session and signed artifact are required" }); return;
        }
        const prefix = "/__local-video-lab__/objects/" + objectStore.bucket;
        const original = "https://" + objectStore.bucket + "." + objectStore.endpointHost + url.pathname.slice(prefix.length) + url.search;
        try {
          if (!url.pathname.startsWith(prefix + "/") || localObjectDownloadUrl(original, objectStore) !== frontendBase + request.url) throw new Error("Invalid object identity");
        } catch { respond(response, 403, { error: "Invalid or expired local artifact link" }); return; }
        const objectURL = new URL(original);
        const headers = { host: objectURL.host };
        if (request.headers.range) headers.range = request.headers.range;
        if (request.headers["if-range"]) headers["if-range"] = request.headers["if-range"];
        const upstream = httpsRequest({ hostname: "127.0.0.1", port: 18440, servername: objectURL.hostname, ca: objectStore.ca,
          rejectUnauthorized: true, method: request.method, path: objectURL.pathname + objectURL.search, headers, timeout: 15_000 }, (result) => {
          if (![200, 206, 304, 403, 404, 416].includes(result.statusCode)) { result.resume(); respond(response, 502, { error: "Local artifact store is not ready" }); return; }
          const safeHeaders = { "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff" };
          for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"]) if (result.headers[name]) safeHeaders[name] = result.headers[name];
          response.writeHead(result.statusCode, safeHeaders); result.pipe(response);
        });
        upstream.on("timeout", () => upstream.destroy(new Error("Object store timeout")));
        upstream.on("error", () => { if (!response.headersSent) respond(response, 502, { error: "Local artifact TLS or storage verification failed" }); else response.destroy(); });
        upstream.end(); return;
      }
      if (url.pathname.startsWith("/api/v1/")) {
        const headers = { ...request.headers, host: "127.0.0.1:18420" };
        for (const name of ["authorization", "x-user-id", "x-platform-admin-user-id", "x-bootstrap-token", "x-internal-service-token", "forwarded", "x-forwarded-host", "x-forwarded-for", "x-forwarded-proto", "connection"]) delete headers[name];
        headers.cookie = isolatedCookieHeader(request.headers.cookie, labId);
        headers["x-local-video-lab-id"] = labId;
        headers["x-local-video-lab-nonce"] = manifest.instance_nonce;
        const upstream = httpRequest(new URL(request.url, manifest.targets.platform_base), { method: request.method, headers, timeout: 30_000 }, (result) => {
          const outputHeaders = { ...result.headers, "cache-control": "no-store" };
          delete outputHeaders["set-cookie"];
          const cookies = (result.headers["set-cookie"] || []).map((item) => isolatedSetCookie(item, labId)).filter(Boolean);
          if (cookies.length) outputHeaders["set-cookie"] = cookies;
          if (request.method === "GET" && result.statusCode === 200 && /\/tasks\/[0-9a-f-]+\/artifacts\/[0-9a-f-]+\/(preview|download)$/.test(url.pathname)) {
            const chunks = []; let size = 0;
            result.on("data", (chunk) => { size += chunk.length; if (size > 64 * 1024) result.destroy(new Error("Oversized artifact metadata")); else chunks.push(chunk); });
            result.on("error", () => { if (!response.headersSent) respond(response, 502, { error: "Artifact metadata is unavailable" }); });
            result.on("end", () => {
              try {
                const metadata = JSON.parse(Buffer.concat(chunks).toString("utf8"));
                metadata.url = localObjectDownloadUrl(metadata.url, objectStore, frontendBase);
                delete outputHeaders["content-length"]; delete outputHeaders["transfer-encoding"]; delete outputHeaders["content-encoding"];
                const body = JSON.stringify(metadata);
                response.writeHead(200, { ...outputHeaders, "content-length": Buffer.byteLength(body), "referrer-policy": "no-referrer" }); response.end(body);
              } catch { respond(response, 502, { error: "Verified artifact is not bound to this local store" }); }
            });
            return;
          }
          response.writeHead(result.statusCode, outputHeaders); result.pipe(response);
        });
        upstream.on("timeout", () => upstream.destroy(new Error("Platform timeout")));
        upstream.on("error", () => { if (!response.headersSent) respond(response, 502, { error: { code: "LOCAL_LAB_PLATFORM_UNAVAILABLE", message: "本地 Platform 暂时不可用，请保留当前草稿。" } }); else response.destroy(); });
        request.pipe(upstream); return;
      }
      if (request.method !== "GET" && request.method !== "HEAD") { respond(response, 405, { error: "Method not allowed" }); return; }
      let path;
      try { path = resolve(root, "." + decodeURIComponent(url.pathname)); } catch { respond(response, 400, { error: "Invalid path" }); return; }
      if (path !== root && !path.startsWith(root + sep)) { respond(response, 403, { error: "Invalid path" }); return; }
      let info;
      try { info = await stat(path); } catch { info = null; }
      if (!info?.isFile()) {
        if (extname(url.pathname)) { respond(response, 404, { error: "Asset not found" }); return; }
        if (!isolatedCookieHeader(request.headers.cookie, labId)) { response.writeHead(303, { Location: "/__local-video-lab__", "Cache-Control": "no-store" }); response.end(); return; }
        const html = injectLabRuntime(await readFile(resolve(root, "index.html"), "utf8"), { frontendBase, companyId: receipt.company_id });
        respond(response, 200, html, "text/html; charset=utf-8"); return;
      }
      if (path === resolve(root, "index.html")) {
        respond(response, 200, injectLabRuntime(await readFile(path, "utf8"), { frontendBase, companyId: receipt.company_id }), "text/html; charset=utf-8"); return;
      }
      response.writeHead(200, { "Content-Type": mediaTypes[extname(path)] || "application/octet-stream", "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff" });
      if (request.method === "HEAD") response.end(); else createReadStream(path).pipe(response);
    } catch {
      if (!response.headersSent) respond(response, 500, { error: "本地联调请求失败；未向真实供应商追加请求。" });
      else response.destroy();
    }
  });
}
