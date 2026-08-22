import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import test from "node:test";
import worker from "../worker/index.js";

test("serves existing static assets without a fallback", async () => {
  const calls = [];
  const response = await worker.fetch(new Request("https://example.test/assets/app.js"), {
    ASSETS: {
      fetch: async (request) => {
        calls.push(new URL(request.url).pathname);
        return new Response("asset", { status: 200 });
      },
    },
  });

  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/assets/app.js"]);
  assert.match(
    response.headers.get("content-security-policy"),
    /frame-ancestors 'none'/,
  );
  const csp = response.headers.get("content-security-policy");
  assert.match(csp, /(?:^|; )connect-src 'self'(?:;|$)/);
  assert.match(csp, /(?:^|; )img-src 'self' data: blob:(?:;|$)/);
  assert.match(csp, /(?:^|; )media-src 'self' blob:(?:;|$)/);
  assert.doesNotMatch(csp, /(?:^|; )(?:connect|img|media)-src[^;]* https:(?:;|\s|$)/);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.match(response.headers.get("strict-transport-security"), /max-age=/);
});

test("allows only the deployment-controlled Platform origin for browser API calls", async () => {
  const response = await worker.fetch(new Request("https://app.example.test/"), {
    PLATFORM_API_ORIGIN: "https://platform.example.test/",
    PLATFORM_SHOWCASE_MEDIA_ORIGIN: "https://showcase-media.example.test/",
    ASSETS: {
      fetch: async () => new Response("app", { status: 200 }),
    },
  });

  const csp = response.headers.get("content-security-policy");
  assert.match(
    csp,
    /(?:^|; )connect-src 'self' https:\/\/platform\.example\.test(?:;|$)/,
  );
  assert.match(
    csp,
    /(?:^|; )img-src 'self' data: blob: https:\/\/platform\.example\.test https:\/\/showcase-media\.example\.test(?:;|$)/,
  );
  assert.match(
    csp,
    /(?:^|; )media-src 'self' blob: https:\/\/platform\.example\.test https:\/\/showcase-media\.example\.test(?:;|$)/,
  );
  assert.doesNotMatch(csp, /connect-src[^;]*showcase-media/);
  assert.doesNotMatch(csp, /connect-src[^;]* https:(?:;|\s|$)/);
});

test("fails closed for malformed or insecure Platform origin bindings", async () => {
  for (const platformOrigin of [
    "http://platform.example.test",
    "https://user:secret@platform.example.test",
    "https://platform.example.test/api",
    "https://platform.example.test?redirect=evil",
  ]) {
    const response = await worker.fetch(
      new Request("https://app.example.test/"),
      {
        PLATFORM_API_ORIGIN: platformOrigin,
        ASSETS: {
          fetch: async () => new Response("app", { status: 200 }),
        },
      },
    );

    assert.match(
      response.headers.get("content-security-policy"),
      /(?:^|; )connect-src 'self'(?:;|$)/,
    );
  }
});

test("fails closed for malformed or insecure showcase media origins", async () => {
  for (const mediaOrigin of [
    "http://showcase-media.example.test",
    "https://user:secret@showcase-media.example.test",
    "https://showcase-media.example.test/media",
    "https://showcase-media.example.test?signature=unsafe",
  ]) {
    const response = await worker.fetch(
      new Request("https://app.example.test/"),
      {
        PLATFORM_API_ORIGIN: "https://platform.example.test",
        PLATFORM_SHOWCASE_MEDIA_ORIGIN: mediaOrigin,
        ASSETS: {
          fetch: async () => new Response("app", { status: 200 }),
        },
      },
    );

    const csp = response.headers.get("content-security-policy");
    assert.match(
      csp,
      /(?:^|; )img-src 'self' data: blob: https:\/\/platform\.example\.test(?:;|$)/,
    );
    assert.match(
      csp,
      /(?:^|; )media-src 'self' blob: https:\/\/platform\.example\.test(?:;|$)/,
    );
    assert.doesNotMatch(csp, /showcase-media\.example\.test/);
  }
});

test("falls back to index.html for an unknown app route", async () => {
  const calls = [];
  const response = await worker.fetch(
    new Request("https://example.test/flow/step-two?source=share", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async (request) => {
          const url = new URL(request.url);
          calls.push(url.pathname + url.search);
          return new Response(url.pathname === "/index.html" ? "app" : "missing", {
            status: url.pathname === "/index.html" ? 200 : 404,
          });
        },
      },
    },
  );

  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/flow/step-two?source=share", "/index.html"]);
});

test("does not turn missing API or write requests into the app shell", async () => {
  for (const request of [
    new Request("https://example.test/api/missing", { headers: { accept: "text/html" } }),
    new Request("https://example.test/health", { headers: { accept: "text/html" } }),
    new Request("https://example.test/flow", { method: "POST", headers: { accept: "text/html" } }),
  ]) {
    let calls = 0;
    const response = await worker.fetch(request, {
      ASSETS: {
        fetch: async () => {
          calls += 1;
          return new Response("missing", { status: 404 });
        },
      },
    });

    assert.equal(response.status, 404);
    assert.equal(calls, 1);
  }
});

test("emits the files required by Sites packaging", async () => {
  await access(new URL("../dist/client/index.html", import.meta.url));
  await access(new URL("../dist/server/index.js", import.meta.url));
  await access(new URL("../dist/.openai/hosting.json", import.meta.url));
});
