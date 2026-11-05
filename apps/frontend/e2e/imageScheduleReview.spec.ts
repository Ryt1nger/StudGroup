import { expect, test } from './fixtures';
import { selectCalendarDate } from './selectCalendarDate';

test('editor corrects the recognized schedule and maps it to one official occurrence', async ({ page }) => {
  await page.goto('/schedule?scenario=schedule-review');
  await page.getByRole('link', { name: 'Управление расписанием' }).click();
  await expect(page.getByRole('heading', { name: 'Управление расписанием', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Загрузить расписание и открыть черновики' }).click();
  await expect(page.getByRole('heading', { name: 'Загрузка расписания', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Редактировать пару: Математический анализ/ }).click();
  const date = await page.getByLabel('Действует с', { exact: true }).inputValue();
  await page.getByLabel('Преподаватель', { exact: true }).fill('Петров');
  await page.getByLabel('Аудитория', { exact: true }).fill('305');
  const mapping = page.getByRole('combobox', { name: 'Заменить существующую пару', exact: true });
  await expect(mapping.locator('option').nth(1)).toBeAttached();
  const target = await mapping
    .locator('option')
    .evaluateAll((options) =>
      options.find((option) => option.textContent?.includes('Математический анализ'))?.getAttribute('value'),
    );
  expect(target).toBeTruthy();
  await mapping.selectOption(target!);
  await expect(page.getByRole('heading', { name: 'Редактирование пары', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Сохранить изменения', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить расписание', exact: true }).click();
  await expect(page).toHaveURL(/\/schedule\/manage$/);
  await page.getByRole('link', { name: 'Назад' }).click();
  await expect(page).toHaveURL(/\/schedule(?:\?|$)/);
  await selectCalendarDate(page, date);
  const changed = page.getByRole('link', { name: /Математический анализ.*13:00.*14:30.*305.*Петров/ });
  await expect(changed).toBeAttached();
  await expect(changed).toHaveCount(1);
});
