import type { Page } from '@playwright/test';

export async function selectCalendarDate(page: Page, date: string) {
  const week = page.getByRole('group', { name: 'День', exact: true });
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const start = await week.getAttribute('data-week');
    if (!start) throw new Error('Calendar week is unavailable');
    const end = new Date(Date.parse(`${start}T12:00:00Z`) + 6 * 86_400_000).toISOString().slice(0, 10);
    if (date >= start && date <= end) {
      await week.getByRole('button', { name: new RegExp(`, ${Number(date.slice(-2))}$`) }).click();
      return;
    }
    await week.focus();
    await week.press(date > end ? 'ArrowRight' : 'ArrowLeft');
  }
  throw new Error(`Calendar did not reach ${date}`);
}
