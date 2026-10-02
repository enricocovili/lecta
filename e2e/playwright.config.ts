import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL || "http://lecta-frontend:4321",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  outputDir: process.env.PW_OUTPUT || "/tmp/e2e-results",
});
