import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/playwright",
  fullyParallel: true,
  forbidOnly: true,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    headless: true,
    trace: "retain-on-failure",
  },
  webServer:
    process.env.COMMERCEVISION_E2E_EXTERNAL_SERVER === "1"
      ? undefined
      : {
          command: "node tests/start-e2e-server.mjs",
          reuseExistingServer: false,
          url: "http://127.0.0.1:3100",
          timeout: 120000,
        },
});
