// Lecta frontend server.
//
// * /api/*      streamed to the backend (no buffering, no body-size limit):
//               plain HTTP, server-sent events, WebSocket upgrades, large uploads.
// * /_astro/*   and other built client assets, served from dist/client.
// * everything else is rendered by Astro (node adapter, middleware mode).
//
// Only node core modules are used for the proxy.
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 4321);
const HOST = process.env.HOST || "0.0.0.0";
const BACKEND = new URL(process.env.LECTA_BACKEND_URL || "http://backend:8000");
const CLIENT_DIR = path.join(here, "dist", "client");

const { handler: astroHandler } = await import("./dist/server/entry.mjs");

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const upstreamAgent = new http.Agent({ keepAlive: true, maxSockets: 64 });

function clientProto(req) {
  const fwd = req.headers["x-forwarded-proto"];
  if (typeof fwd === "string" && fwd) return fwd.split(",")[0].trim();
  return req.socket.encrypted ? "https" : "http";
}

function forwardedHeaders(req, { keepUpgrade = false } = {}) {
  const out = {};
  for (const [k, v] of Object.entries(req.headers)) {
    if (!keepUpgrade && HOP_BY_HOP.has(k)) continue;
    out[k] = v;
  }
  const remote = req.socket.remoteAddress || "";
  const prior = req.headers["x-forwarded-for"];
  out["x-forwarded-for"] = prior ? `${prior}, ${remote}` : remote;
  out["x-forwarded-proto"] = clientProto(req);
  out["x-forwarded-host"] = req.headers["x-forwarded-host"] || req.headers.host || "";
  return out;
}

function proxyHttp(req, res) {
  const upstream = http.request(
    {
      protocol: BACKEND.protocol,
      hostname: BACKEND.hostname,
      port: BACKEND.port || 80,
      method: req.method,
      path: req.url,
      headers: forwardedHeaders(req),
      agent: upstreamAgent,
    },
    (upRes) => {
      const headers = {};
      for (const [k, v] of Object.entries(upRes.headers)) {
        if (!HOP_BY_HOP.has(k)) headers[k] = v;
      }
      if ((upRes.headers["content-type"] || "").startsWith("text/event-stream")) {
        headers["x-accel-buffering"] = "no";
        headers["cache-control"] = "no-store";
      }
      res.writeHead(upRes.statusCode || 502, upRes.statusMessage, headers);
      res.flushHeaders();
      upRes.pipe(res);
      upRes.on("error", () => res.destroy());
    },
  );
  upstream.on("error", (err) => {
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ detail: "Backend unavailable" }));
    } else {
      res.destroy(err);
    }
  });
  // Abort the upstream request if the client goes away mid-stream (SSE, uploads).
  res.on("close", () => {
    if (!res.writableFinished) upstream.destroy();
  });
  req.pipe(upstream);
}

function proxyUpgrade(req, socket, head) {
  const upstream = net.connect(Number(BACKEND.port || 80), BACKEND.hostname, () => {
    const headers = forwardedHeaders(req, { keepUpgrade: true });
    let raw = `${req.method} ${req.url} HTTP/1.1\r\n`;
    for (const [k, v] of Object.entries(headers)) {
      for (const one of Array.isArray(v) ? v : [v]) raw += `${k}: ${one}\r\n`;
    }
    raw += "\r\n";
    upstream.write(raw);
    if (head && head.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  const close = () => {
    upstream.destroy();
    socket.destroy();
  };
  upstream.on("error", close);
  socket.on("error", close);
  upstream.on("close", () => socket.destroy());
  socket.on("close", () => upstream.destroy());
}

// ------------------------------------------------------------------ static assets

const MIME = {
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
  ".json": "application/json",
  ".woff2": "font/woff2",
  ".wasm": "application/wasm",
  ".map": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".bcmap": "application/octet-stream",
  ".pfb": "application/octet-stream",
  ".ttf": "font/ttf",
};

function tryStatic(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") return false;
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url, "http://x").pathname);
  } catch {
    return false;
  }
  if (pathname === "/" || pathname.endsWith("/")) return false;
  const file = path.resolve(CLIENT_DIR, "." + pathname);
  if (!file.startsWith(CLIENT_DIR + path.sep)) return false;
  let st;
  try {
    st = fs.statSync(file);
  } catch {
    return false;
  }
  if (!st.isFile()) return false;
  const ext = path.extname(file).toLowerCase();
  const immutable = pathname.startsWith("/_astro/");
  res.writeHead(200, {
    "content-type": MIME[ext] || "application/octet-stream",
    "content-length": st.size,
    "cache-control": immutable ? "public, max-age=31536000, immutable" : "public, max-age=3600",
    "x-content-type-options": "nosniff",
  });
  if (req.method === "HEAD") res.end();
  else fs.createReadStream(file).pipe(res);
  return true;
}

// ------------------------------------------------------------------ pages

function pageSecurityHeaders(req, res) {
  res.setHeader("x-content-type-options", "nosniff");
  res.setHeader("referrer-policy", "same-origin");
  res.setHeader("x-frame-options", "DENY");
  // Script/style policies are emitted by Astro as a <meta> CSP with hashes;
  // frame-ancestors can only be set as a header.
  res.setHeader("content-security-policy", "frame-ancestors 'none'");
  res.setHeader("permissions-policy", "camera=(self), microphone=(), geolocation=(), interest-cohort=()");
  // COOP and HSTS are only meaningful (and only accepted) over HTTPS.
  if (clientProto(req) === "https") {
    res.setHeader("cross-origin-opener-policy", "same-origin");
    res.setHeader("strict-transport-security", "max-age=31536000");
  }
}

const server = http.createServer((req, res) => {
  const url = req.url || "/";
  if (url === "/api" || url.startsWith("/api/") || url.startsWith("/api?")) {
    proxyHttp(req, res);
    return;
  }
  if (url === "/healthz") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok");
    return;
  }
  if (tryStatic(req, res)) return;
  pageSecurityHeaders(req, res);
  astroHandler(req, res, undefined, {});
});

server.on("upgrade", (req, socket, head) => {
  if ((req.url || "").startsWith("/api/")) proxyUpgrade(req, socket, head);
  else socket.destroy();
});

// Large uploads and long SSE streams: no request timeout, generous keep-alive.
server.requestTimeout = 0;
server.headersTimeout = 60_000;
server.keepAliveTimeout = 75_000;
server.timeout = 0;

server.listen(PORT, HOST, () => {
  console.log(`lecta-frontend listening on http://${HOST}:${PORT} (backend ${BACKEND.origin})`);
});

for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  });
}
