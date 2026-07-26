// {{name}} — a minimal mini-cloud Node service (zero-dependency http).
// Reads canonical env (PORT), exposes /healthz, /readyz, /metrics. The Phase-5 TS SDK adds config,
// a typed client, DB/storage, and richer observability; wire them in when available.
import http from "node:http";

const PORT = Number(process.env.PORT ?? {{api_port}});
const APP_NAME = process.env.APP_NAME ?? "{{name}}";

let requests = 0;

const server = http.createServer((req, res) => {
  requests += 1;
  const send = (code, body, type = "application/json") => {
    res.writeHead(code, { "content-type": type });
    res.end(typeof body === "string" ? body : JSON.stringify(body));
  };

  switch (req.url) {
    case "/healthz":
      return send(200, { status: "ok" });
    case "/readyz":
      // No backing services wired yet (Phase 5). Liveness-equivalent for now.
      return send(200, { ready: true, checks: {} });
    case "/metrics":
      // Minimal Prometheus exposition; the TS SDK's obs replaces this with the standard set.
      return send(
        200,
        `# HELP http_requests_total Total HTTP requests.\n` +
          `# TYPE http_requests_total counter\n` +
          `http_requests_total{app="${APP_NAME}"} ${requests}\n`,
        "text/plain",
      );
    case "/":
      return send(200, { app: APP_NAME, metrics: "/metrics" });
    default:
      return send(404, { error: "not found" });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(JSON.stringify({ level: "info", app: APP_NAME, msg: `listening on :${PORT}` }));
});
