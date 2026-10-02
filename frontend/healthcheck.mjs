import http from "node:http";
const req = http.get({ host: "127.0.0.1", port: Number(process.env.PORT || 4321), path: "/healthz", timeout: 4000 }, (res) => {
  process.exit(res.statusCode === 200 ? 0 : 1);
});
req.on("error", () => process.exit(1));
req.on("timeout", () => process.exit(1));
