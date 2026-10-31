import { expect, test } from './fixtures';

// The fixture describes Monday at 09:00 Moscow; real timers/animations continue normally.
test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-05T06:00:00Z'));
});

/**
 * Critical journey: Today → homework detail → «Выполнено мной», including a revision conflict.
 * Runs against the dev-only Telegram and API mocks (see playwright.config.ts).
 */

test('Today → details → mark and unmark «Выполнено мной»', async ({ page }) => {
  await page.goto('/?scenario=populated');
  await expect(page.getByRole('heading', { name: 'Сегодня', level: 1 })).toBeVisible();

  await page.getByRole('link', { name: /ДЗ №12–140/ }).click();
  await expect(page).toHaveURL(/\/homework\//);
  await expect(page.getByRole('heading', { name: 'ДЗ №12–140' })).toBeVisible();

  await page.getByRole('button', { name: 'Выполнено мной', exact: true }).click();
  const undo = page.getByRole('button', { name: /Снять отметку/ });
  await expect(undo).toBeVisible();
  await expect(undo).toBeEnabled();

  await undo.click();
  await expect(page.getByRole('button', { name: 'Выполнено мной', exact: true })).toBeEnabled();
});

test('revision conflict: the card is refetched and the repeated action succeeds', async ({ page }) => {
  await page.goto('/?scenario=completion-conflict');
  await page.getByRole('link', { name: /ДЗ №12–140/ }).click();

  await page.getByRole('button', { name: 'Выполнено мной', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('изменились');

  // After the forced refetch the action is available again, with the new revision.
  const retry = page.getByRole('button', { name: 'Выполнено мной', exact: true });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(page.getByRole('button', { name: /Снять отметку/ })).toBeVisible();
});

test('a failed write is rolled back and reported', async ({ page }) => {
  await page.goto('/?scenario=completion-fails');
  await page.getByRole('link', { name: /ДЗ №12–140/ }).click();
  await page.getByRole('button', { name: 'Выполнено мной', exact: true }).click();
  await expect(page.getByText('Не удалось сохранить отметку. Попробуйте ещё раз.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Выполнено мной', exact: true })).toBeVisible();
});

test('schedule opens from the navigation and shows lessons', async ({ page }) => {
  await page.goto('/?scenario=populated');
  await page.getByRole('link', { name: 'Расписание' }).click();
  await expect(page.getByRole('heading', { name: 'Расписание', level: 1 })).toBeAttached();
  await page.getByRole('group', { name: 'День' }).getByRole('button', { name: /^Пн,/ }).click();
  await expect(page.locator('.ev').last()).toBeInViewport();
});

test('Today lesson opens the subject page for that lesson', async ({ page }) => {
  await page.goto('/?scenario=populated');
  const card = page.getByRole('link', { name: /пара:/ });
  await expect(card).toBeVisible();
  await card.click();
  await page.getByRole('link', { name: /Всё по предмету/ }).click();
  await expect(page.getByRole('heading', { name: 'Математический анализ', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Домашние задания' })).toBeVisible();

  await page.goto('/?scenario=next-null');
  await expect(page.getByRole('link', { name: /ДЗ №12–140/ })).toBeVisible();
  await expect(page.getByText('Следующая пара')).toHaveCount(0);
});

test('Tasks: archive rules, filters, card → details → mark syncs the list', async ({ page }) => {
  await page.goto('/?scenario=populated');
  await page.getByRole('link', { name: 'Задания' }).click();
  await expect(page.getByRole('heading', { name: 'Задания', level: 1 })).toBeVisible();

  // Past and cancelled items are not in the active views.
  await expect(page.getByRole('link', { name: /Открыть задание: КТ по истории/ })).toBeVisible();
  for (const title of ['Реферат по социологии', 'Конспект по философии', 'Доклад по экологии']) {
    await expect(page.getByText(title)).toHaveCount(0);
  }
  await page.getByRole('button', { name: 'Сегодня' }).click();
  await expect(page.getByText('Реферат по социологии')).toHaveCount(0);

  // They are available only in the archive.
  await page.getByRole('button', { name: 'Архив' }).click();
  await expect(page.getByRole('link', { name: /Открыть задание: Реферат по социологии/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Открыть задание: Доклад по экологии/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Открыть задание: КТ по истории/ })).toHaveCount(0);

  // Completing an active item moves it to «Выполнено мной» and the list restarts.
  await page.getByRole('button', { name: 'Все' }).click();
  await page.getByRole('link', { name: /Открыть задание: КТ по истории/ }).click();
  await expect(page.getByRole('heading', { name: 'КТ по истории', level: 1 })).toBeVisible();
  await expect(page.getByText('Проверяем обновление')).toHaveCount(0);
  await page.getByRole('button', { name: 'Выполнено мной', exact: true }).click();
  await expect(page.getByRole('button', { name: /Снять отметку/ })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Задания', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: 'Выполнено мной', exact: true }).click();
  const completed = page.getByRole('link', { name: /Открыть задание: КТ по истории/ });
  await expect(completed).toBeVisible();
  await expect(completed.getByRole('button', { name: /Снять отметку/ })).toHaveAttribute('aria-pressed', 'true');
});

test('Tasks: server pages load when scrolling to the end', async ({ page }) => {
  await page.goto('/?scenario=tasks-many');
  await page.getByRole('link', { name: 'Задания' }).click();
  await expect(page.getByRole('heading', { name: 'Задания', level: 1 })).toBeVisible();
  const cards = page.getByRole('link', { name: /Открыть задание/ });
  await expect(cards).toHaveCount(50);
  await page.locator('.sentinel').scrollIntoViewIfNeeded();
  await expect(cards).toHaveCount(100);
  await page.locator('.sentinel').scrollIntoViewIfNeeded();
  await expect(cards).toHaveCount(124);
  await expect(page.locator('.sentinel')).toHaveCount(0);
});
