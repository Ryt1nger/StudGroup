import { expect, test } from './fixtures';
import { selectCalendarDate } from './selectCalendarDate';

test('editor corrects the recognized schedule and maps it to one official occurrence', async ({ page }) => {
  await page.goto('/schedule?scenario=schedule-review');
  await page.getByRole('link', { name: 'Проверить распознанное расписание' }).click();
  await expect(page.getByRole('heading', { name: 'Проверка расписания', exact: true })).toBeVisible();
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
  await page.getByRole('button', { name: 'Подтвердить расписание', exact: true }).click();
  await expect(page).toHaveURL(/\/schedule$/);
  await selectCalendarDate(page, date);
  const changed = page.getByRole('link', { name: /Математический анализ.*13:00.*14:30.*305.*Петров/ });
  await expect(changed).toBeAttached();
  await expect(changed).toHaveCount(1);
});
