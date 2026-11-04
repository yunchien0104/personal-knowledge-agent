import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "ENABLE_AI_E2E_HARNESS=1 NEXT_TELEMETRY_DISABLED=1 npm run build && ENABLE_AI_E2E_HARNESS=1 NEXT_TELEMETRY_DISABLED=1 npm run start -- --hostname 127.0.0.1 --port 3100",
    env: {
      ENABLE_AI_E2E_HARNESS: "1",
      NEXT_TELEMETRY_DISABLED: "1",
    },
    reuseExistingServer: false,
    timeout: 120_000,
    url: "http://127.0.0.1:3100/ai-editor-e2e",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
