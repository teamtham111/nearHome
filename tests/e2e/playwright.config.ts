import { defineConfig, devices } from "@playwright/test";

const WEB_URL = process.env.WEB_URL ?? "http://localhost:3000";
const API_URL = process.env.API_URL ?? "http://localhost:8000";
const WEB_PORT = new URL(WEB_URL).port || "3000";
const API_PORT = new URL(API_URL).port || "8000";

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: WEB_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.CI
    ? undefined
    : [
        {
          command: `cd ../../apps/api && source .venv/bin/activate && CORS_ORIGINS=${WEB_URL} uvicorn app.main:app --port ${API_PORT}`,
          url: `${API_URL}/api/v1/health`,
          reuseExistingServer: true,
          timeout: 120000,
        },
        {
          command: `cd ../../apps/web && SKIP_ROOT_ENV=1 NEXT_PUBLIC_API_BASE_URL=${API_URL} NEXT_PUBLIC_DEPLOYMENT_ENV=development npm run dev -- --port ${WEB_PORT}`,
          url: WEB_URL,
          reuseExistingServer: true,
          timeout: 120000,
        },
      ],
});
