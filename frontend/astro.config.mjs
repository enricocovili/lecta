// @ts-check
import node from "@astrojs/node";
import react from "@astrojs/react";
import { defineConfig } from "astro/config";

// SSR build mounted by server.mjs (Node adapter in middleware mode).
export default defineConfig({
  output: "server",
  adapter: node({ mode: "middleware", bodySizeLimit: 0 }),
  integrations: [react()],
  devToolbar: { enabled: false },
  build: { inlineStylesheets: "never" },
  security: {
    checkOrigin: true,
    csp: {
      algorithm: "SHA-256",
      directives: [
        "default-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
      ],
      scriptDirective: { resources: ["'self'", "'wasm-unsafe-eval'"] },
      // React style props and CodeMirror's runtime styles need inline styles.
      styleDirective: { resources: ["'self'", "'unsafe-inline'"] },
    },
  },
  vite: {
    build: { chunkSizeWarningLimit: 4000 },
  },
});
