import { expect, test } from '@playwright/test';

test('Today, Schedule and Subjects fit narrow mobile viewports', async ({ page }) => {
  for (const width of [320, 360, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 780 });
    for (const route of ['/today', '/schedule', '/subjects']) {
      await page.goto(route);
      await expect(page.locator('.view')).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
});
