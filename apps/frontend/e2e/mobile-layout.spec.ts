import { expect, test } from '@playwright/test';

test('Today, Schedule and Subjects fit narrow mobile viewports', async ({ page }) => {
  for (const width of [320, 360, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 780 });
    for (const route of ['/today', '/schedule', '/subjects', '/tasks', '/profile']) {
      await page.goto(route);
      await expect(page.locator('.view')).toBeVisible();
      await expect.poll(() => page.evaluate(() => {
        const overflow = [...document.querySelectorAll('.scroll, .view, .top, .nav')]
          .filter(el => el.scrollWidth > el.clientWidth + 1)
          .map(el => ({ element: el.className, width: el.clientWidth, content: el.scrollWidth }));
        if (document.documentElement.scrollWidth > innerWidth) {
          overflow.push({ element: 'document', width: innerWidth, content: document.documentElement.scrollWidth });
        }
        return overflow;
      }), { message: `Horizontal overflow at ${width}px on ${route}` }).toEqual([]);
    }
  }
});
