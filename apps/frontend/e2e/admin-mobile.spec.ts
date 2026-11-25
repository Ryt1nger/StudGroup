import { readFileSync } from 'node:fs';
import { expect, test } from './fixtures';

const assets = new URL('../../backend/src/studgroup/admin_ui/', import.meta.url);
const host = 'https://admin-layout.test';

test('mobile admin cards, actions, support frame and process controls stay within the viewport', async ({ page }) => {
  const processes = ['ai', 'sdo', 'telegram_outbox', 'scheduler'].map(name => ({
    name, status: 'running', effective_enabled: true, error_active: false,
  }));
  const groupName = 'Учебная группа с очень длинным названием без сокращений';
  await page.addInitScript(() => {
    // Model older WebViews that lack the newer Object.hasOwn helper.
    Object.defineProperty(Object, 'hasOwn', { value: undefined, configurable: true });
  });
  await page.route(host + '/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const name = path === '/' ? 'index.html' : path.split('/').pop() ?? '';
    if (['index.html', 'app.js', 'style.css', 'visuals.js'].includes(name)) {
      const contentType = name.endsWith('.js') ? 'application/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html';
      await route.fulfill({ contentType, body: readFileSync(new URL(name, assets), 'utf8') });
      return;
    }
    if (path === '/v1/admin/users') {
      await route.fulfill({ json: { items: [{
        id: 'user', first_name: 'Имя', last_name: 'Фамилия',
        username: 'a'.repeat(90), telegram_user_id: 123456789, sdo_connected: true,
        memberships: [{ name: groupName, group_id: 'group', role: 'student', status: 'active', group_status: 'active' }],
      }], next_offset: null } });
      return;
    }
    if (path === '/v1/admin/processes') {
      await route.fulfill({ json: { processes, queues: { ai_messages: { queued: 5 } } } });
      return;
    }
    if (path === '/v1/admin/sdo-runs') {
      await route.fulfill({ json: { run: null } });
      return;
    }
    if (path === '/v1/admin/support-sessions') {
      await route.fulfill({ json: { app_url: 'https://student-layout.test/?support=1' } });
      return;
    }
    await route.fulfill({ status: 404, json: { error: { message: 'Unexpected mock route' } } });
  });
  await page.route('https://student-layout.test/**', route => route.fulfill({
    contentType: 'text/html',
    body: '<meta name="viewport" content="width=device-width,initial-scale=1"><main>Student preview</main>',
  }));
  await page.setViewportSize({ width: 320, height: 780 });
  await page.goto(host);
  // Authenticate only the mocked browser harness; no real credentials or production requests.
  await page.evaluate("token = 'mock'; document.getElementById('dashboard').hidden = false; selectPage('audience'); loadAdminUsers()");
  const card = page.locator('#admin-users .billing-option').first();
  await expect(card).toBeVisible();
  const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
  for (const width of [320, 360, 390, 430, 1280]) {
    await page.setViewportSize({ width, height: 780 });
    await expect.poll(fits).toBe(true);
    for (const control of await card.getByRole('button').all()) {
      await expect.poll(() => control.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    }
  }
  await page.setViewportSize({ width: 320, height: 780 });
  await page.evaluate("window.Telegram = { WebApp: { initData: 'mock', colorScheme: 'dark' } }; openSupport('user', 'group', 'view')");
  const frame = page.locator('.support-frame');
  await expect(frame).toBeVisible();
  await expect.poll(() => frame.evaluate(node => node.getBoundingClientRect().right <= innerWidth + 1)).toBe(true);
  await page.getByRole('button', { name: 'В админку', exact: true }).click();
  await expect(frame).toHaveCount(0);
  await page.evaluate("selectPage('processes'); loadProcessControls()");
  await expect(page.locator('#process-cards .process-card')).toHaveCount(4);
  await expect(page.getByRole('switch')).toHaveCount(4);
  await expect(page.locator('#process-queues')).toContainText('AI / сообщения');
  await expect.poll(fits).toBe(true);
});
