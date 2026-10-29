import { test as base, expect } from '@playwright/test';

// These journeys explicitly use the local Telegram adapter. A blocking remote SDK script
// must not turn CDN availability into a test of our UI. Production still loads the real bridge.
export const test = base.extend<{ localBridge: void }>({
  localBridge: [async ({ page }, use) => {
    await page.route('https://telegram.org/js/telegram-web-app.js', (route) =>
      route.fulfill({ status: 200, contentType: 'application/javascript', body: '' }),
    );
    await use();
  }, { auto: true }],
});

export { expect };
