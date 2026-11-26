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


// Outer scroll width alone misses overlapping child columns.
test('Lesson time and title never overlap at mobile widths', async ({ page }) => {
  await page.goto('/today');
  await expect(page.locator('.week')).toBeVisible();
  let checked = 0;
  for (const width of [320, 360, 390, 430]) {
    await page.setViewportSize({ width, height: 780 });
    for (const day of await page.locator('.week .wd').all()) {
      await day.click();
      const rows = page.locator('.task.lrow');
      for (const row of await rows.all()) {
        await expect(row).toBeVisible();
        const bounds = await row.evaluate((el) => {
          const time = el.querySelector('.tm')!.getBoundingClientRect();
          const body = el.querySelector('.tb')!.getBoundingClientRect();
          return { gap: body.left - time.right, width: body.width };
        });
        expect(bounds.gap).toBeGreaterThanOrEqual(8);
        expect(bounds.width).toBeGreaterThan(0);
        checked++;
      }
    }
  }
  expect(checked).toBeGreaterThan(0);
});
