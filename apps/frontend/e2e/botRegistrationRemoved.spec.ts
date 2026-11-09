import { expect, test } from './fixtures';

test('obsolete onboarding route goes to the ordinary Today app', async ({ page }) => {
  await page.goto('/onboarding?scenario=populated');
  await expect(page.getByRole('heading', { name: 'Сегодня', level: 1 })).toBeVisible();
  await expect(page.getByText('Настроим StudGroup под вас')).toHaveCount(0);
});

test('profile retains SDO while first-launch preview is absent', async ({ page }) => {
  await page.goto('/profile?scenario=populated');
  await expect(page.getByText('Подключение СДО / ЛК')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Посмотреть первый запуск' })).toHaveCount(0);
});
