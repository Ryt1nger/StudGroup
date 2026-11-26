import { expect, test } from './fixtures';
import { selectCalendarDate } from './selectCalendarDate';

for (const theme of ['light', 'dark']) {
  test(`event draft → publication → calendar → move → cancellation (${theme})`, async ({
    page,
    context,
  }, info) => {
    await context.addCookies([{ name: 'studgroup_theme', value: theme, url: 'http://127.0.0.1:5173' }]);
    await page.goto('/events?scenario=events');
    await expect(page.getByText('Пока нет событий')).toBeVisible();
    await expect(page.getByRole('navigation')).toHaveCSS('display', 'grid');
    await expect(page.getByRole('navigation')).toHaveCSS('position', 'absolute');
    await expect(page.getByRole('link', { name: 'Проверить черновики и изменения' })).toHaveCount(0);
    await page.goto('/events/review?scenario=events');
    await page.getByRole('link', { name: /Открытая лекция о космосе/ }).click();
    await expect(page.getByLabel('Название')).toHaveValue('Открытая лекция о космосе');
    const original = await page.getByLabel('Дата начала', { exact: true }).inputValue();
    await page.getByRole('button', { name: 'Подтвердить публикацию' }).click();
    await expect(page.getByRole('heading', { name: 'Открытая лекция о космосе', level: 1 })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.getByText('Опубликовано', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Обсудим исследования Луны.', { exact: true })).toBeVisible();
    await page.getByRole('navigation').getByRole('link', { name: 'События', exact: true }).click();
    await expect(page.locator('.group-event-card')).not.toContainText('Обсудим исследования Луны.');
    await expect(page.locator('.group-event-card')).not.toContainText('Опубликовано');
    await page.getByRole('link', { name: /Открытая лекция о космосе/ }).click();
    await page.screenshot({ animations: 'disabled', path: info.outputPath(`event-details-${theme}.png`) });
    await page.getByRole('link', { name: 'Расписание', exact: true }).click();
    await selectCalendarDate(page, original);
    await expect(page.getByRole('heading', { name: 'Открытая лекция о космосе' })).toBeInViewport();
    const compact = page.locator('.schedule-event-card').filter({ hasText: 'Открытая лекция о космосе' });
    await expect(compact).toBeVisible();
    await expect(compact).toHaveClass(/\bev\b/);
    await expect(compact.locator('.bar')).toBeVisible();
    await expect(compact).toHaveCSS('border-radius', '20px');
    expect(await compact.evaluate((el) => getComputedStyle(el).borderLeftWidth)).toBe(await compact.evaluate((el) => getComputedStyle(el).borderRightWidth));
    await expect(compact).not.toContainText('Обсудим исследования Луны.');
    await expect(compact).not.toContainText('Источник в Telegram');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('.tline .ev').first()).toBeVisible();
    const geometry = await page.evaluate(() => {
      const pinned = document.querySelector<HTMLElement>('.pinned')!;
      const view = document.querySelector<HTMLElement>('.view')!;
      const week = pinned.querySelector<HTMLElement>('.week')!;
      const card = document.querySelector<HTMLElement>('.schedule-event-card')!;
      const v = view.getBoundingClientRect();
      const w = week.getBoundingClientRect();
      const b = card.getBoundingClientRect();
      const lesson = document.querySelector<HTMLElement>('.tline .ev')!;
      const props = ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderRadius', 'gap'] as const;
      const mismatched = props.filter(prop => getComputedStyle(card)[prop] !== getComputedStyle(lesson)[prop]);
      const l = lesson.getBoundingClientRect();
      return { weekLeft: w.left - v.left, weekRight: v.right - w.right, cardLeft: b.left - v.left, cardRight: v.right - b.right, lessonDelta: Math.max(Math.abs(l.left - b.left), Math.abs(l.right - b.right)), mismatched };
    });
    expect(Math.abs(geometry.weekLeft)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.weekRight)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.cardLeft - 58)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.cardRight)).toBeLessThanOrEqual(1);
    expect(geometry.lessonDelta).toBeLessThanOrEqual(1);
    expect(geometry.mismatched).toEqual([]);
    await page.screenshot({ animations: 'disabled', path: info.outputPath(`calendar-event-${theme}.png`) });
    await page.getByRole('link', { name: /Открытая лекция о космосе/ }).click();
    await page.getByRole('link', { name: 'Редактировать', exact: true }).click();
    const moved = new Date(Date.parse(`${original}T12:00:00Z`) + 2 * 86_400_000).toISOString().slice(0, 10);
    await page.getByLabel('Дата начала', { exact: true }).fill(moved);
    await page.getByRole('button', { name: 'Сохранить изменения' }).click();
    await expect(page.getByText('Перенесено', { exact: true })).toBeVisible();
    await page.getByRole('link', { name: 'Расписание', exact: true }).click();
    await selectCalendarDate(page, original);
    await expect(page.getByRole('heading', { name: 'Открытая лекция о космосе' })).toHaveCount(0);
    await selectCalendarDate(page, moved);
    await expect(page.getByRole('heading', { name: 'Открытая лекция о космосе' })).toBeInViewport();
    await page.getByRole('link', { name: /Открытая лекция о космосе/ }).click();
    await page.getByRole('link', { name: 'Редактировать', exact: true }).click();
    await page.getByRole('button', { name: 'Отменить событие', exact: true }).click();
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Отменить событие', exact: true })
      .click();
    await expect(page.getByText('Мероприятие отменено')).toBeVisible();
    await page.getByRole('navigation').getByRole('link', { name: 'События', exact: true }).click();
    await expect(page.getByText('Пока нет событий')).toBeVisible();
    await expect(page.getByRole('navigation')).toHaveCSS('display', 'grid');
    await expect(page.getByRole('navigation')).toHaveCSS('position', 'absolute');
    await page.getByRole('button', { name: 'История', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Открытая лекция о космосе' })).toBeInViewport();
    await page.getByRole('link', { name: /Открытая лекция о космосе/ }).click();
    await page.getByRole('button', { name: 'Удалить событие', exact: true }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Удалить', exact: true }).click();
    await expect(page).toHaveURL(/\/events$/);
    await page.getByRole('button', { name: 'История', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Открытая лекция о космосе' })).toHaveCount(0);
  });
}


test('five navigation items fit a narrow mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/events?scenario=events');
  await expect(page.getByText('Пока нет событий')).toBeVisible();
    await expect(page.getByRole('navigation')).toHaveCSS('display', 'grid');
    await expect(page.getByRole('navigation')).toHaveCSS('position', 'absolute');
  const links = page.getByRole('navigation').getByRole('link');
  await expect(links).toHaveCount(5);
  for (const link of await links.all()) {
    const size = await link.evaluate((node) => ({ label: node.textContent, scroll: node.scrollWidth, width: node.clientWidth }));
    expect(size.scroll, JSON.stringify(size)).toBeLessThanOrEqual(size.width + 1);
  }
});
