import { defineConfig, devices } from '@playwright/test';

/**
 * E2E runs against the dev server with the Telegram and API mocks (dev-only). Set CHROMIUM_PATH
 * to use a system Chromium when Playwright's own browser cannot be downloaded.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    ...devices['iPhone 13'],
    browserName: 'chromium',
    launchOptions: { executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'pnpm dev:mock --host 127.0.0.1 --port 5173 --strictPort',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
