import { createServer, connect } from "node:net";

// Docker internal networks intentionally do not publish host ports. This tiny
// dual-network edge exposes only three fixed loopback-published TCP targets;
// providers, databases and workers remain on the isolated internal network.
const mode = process.env.LOCAL_VIDEO_LAB_MODE;
if (!["mock", "live"].includes(mode) || process.env.LOCAL_VIDEO_LAB_ENVIRONMENT !== "development") throw new Error("Explicit development video lab required");
const prefix = mode === "mock" ? "11.254.93" : "11.254.94";
const servers = [];
for (const [listenPort, host, port] of [[8000, prefix + ".20", 8000], [3000, prefix + ".10", 3000], [443, prefix + ".40", 443]]) {
  const server = createServer((client) => {
    const upstream = connect({ host, port });
    const close = () => { client.destroy(); upstream.destroy(); };
    client.setTimeout(120_000, close); upstream.setTimeout(120_000, close);
    client.on("error", close); upstream.on("error", close);
    client.on("close", () => upstream.destroy()); upstream.on("close", () => client.destroy());
    client.pipe(upstream); upstream.pipe(client);
  });
  server.on("error", () => { process.stderr.write("Local video edge failed; no targets were changed.\n"); process.exitCode = 1; for (const item of servers) item.close(); });
  servers.push(server);
  server.listen(listenPort, "0.0.0.0");
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => { for (const server of servers) server.close(); });
