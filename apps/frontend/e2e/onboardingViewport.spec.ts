import { expect, test } from './fixtures';

/**
 * The Telegram toolbar is drawn over the top of Android WebViews. Verify that
 * every onboarding scene, including its CTA, fits in the remaining area.
 * The preview uses the same Shell as first-run, but writes no user data.
 */
test('all eight onboarding screens fit a phone without page scrolling or clipped actions', async ({ page }) => {
  for (const viewport of [
    { width: 320, height: 680 },
    { width: 360, height: 740 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/profile?scenario=populated');
    await page.getByRole('button', { name: 'Посмотреть первый запуск' }).click();

    const transitions = [
      ['Настроим StudGroup под вас', 'Начать настройку'],
      ['Как к вам обращаться?', 'Далее'],
      ['Контакты для связи', 'Далее'],
      ['Подгруппы по предметам', 'Сохранить и продолжить'],
      ['СДО и личный кабинет', 'Продолжить'],
      ['Почти готово', 'Продолжить'],
      ['Подстроим приложение под вас', 'Сохранить оформление'],
      ['Настройка завершена!', 'Вернуться в приложение'],
    ] as const;

    for (const [heading, action] of transitions) {
      await expect(page.getByRole('heading', { name: heading })).toBeVisible();
      const button = page.getByRole('button', { name: action });
      await expect(button).toBeVisible();
      const sizes = await page.evaluate(() => {
        const root = document.querySelector('.ob-root') as HTMLElement;
        const card = document.querySelector('.ob-card') as HTMLElement;
        const scene = document.querySelector('.ob-scene') as HTMLElement;
        const primary = document.querySelector('.ob-primary') as HTMLElement;
        const headingNode = scene.querySelector('h1') as HTMLElement;
        const buttonRect = primary.getBoundingClientRect();
        const headingRect = headingNode.getBoundingClientRect();
        return {
          rootOverflow: root.scrollHeight - root.clientHeight,
          cardOverflow: card.scrollHeight - card.clientHeight,
          sceneOverflow: scene.scrollHeight - scene.clientHeight,
          buttonTop: buttonRect.top,
          buttonBottom: buttonRect.bottom,
          headingTop: headingRect.top,
          viewport: window.innerHeight,
          horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
          artHeight: (document.querySelector('.ob-art') as HTMLElement).getBoundingClientRect().height,
        };
      });
      expect(sizes.rootOverflow, `${viewport.width}x${viewport.height} ${heading}: root`).toBeLessThanOrEqual(2);
      expect(sizes.cardOverflow, `${viewport.width}x${viewport.height} ${heading}: card`).toBeLessThanOrEqual(2);
      expect(sizes.sceneOverflow, `${viewport.width}x${viewport.height} ${heading}: scene`).toBeLessThanOrEqual(2);
      expect(sizes.horizontalOverflow).toBeLessThanOrEqual(1);
      expect(sizes.headingTop).toBeGreaterThanOrEqual(70);
      expect(sizes.buttonTop).toBeGreaterThan(sizes.headingTop);
      expect(sizes.buttonBottom).toBeLessThanOrEqual(sizes.viewport);
      expect(sizes.artHeight).toBeLessThanOrEqual(80);
      await button.click();
    }
    await expect(page.getByRole('button', { name: 'Посмотреть первый запуск' })).toBeVisible();
  }
});
