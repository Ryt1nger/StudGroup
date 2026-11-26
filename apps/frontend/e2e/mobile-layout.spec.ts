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
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const day = new Date();
  day.setUTCDate(day.getUTCDate() + 1);
  while (day.getUTCDay() === 0 || day.getUTCDay() === 6) day.setUTCDate(day.getUTCDate() + 1);
  const date = day.toISOString().slice(0, 10);
  await page.goto(`/today?date=${date}`);
  const rows = page.locator('.task.lrow');
  await expect(rows.first()).toBeVisible();
  for (const width of [320, 360, 390, 430]) {
    await page.setViewportSize({ width, height: 780 });
    await expect.poll(() => rows.evaluateAll(els => els.map(el => {
      const time = el.querySelector('.tm')!.getBoundingClientRect();
      const body = el.querySelector('.tb')!.getBoundingClientRect();
      return body.left - time.right >= 8 && body.width > 0;
    }))).not.toContain(false);
    expect(await rows.count()).toBeGreaterThan(0);
  }
});
