import { expect, test } from './fixtures';

test('cached Today is visible after verified login before a slow refresh finishes', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-05T06:00:00Z'));
  await page.goto('/?scenario=populated');
  await expect(page.getByRole('heading', { name: 'Сегодня', level: 1 })).toBeVisible();
  await expect(page.getByText('ДЗ №12–140').first()).toBeVisible();

  // Wait until encrypted IndexedDB storage is committed; never persist plaintext data.
  await expect.poll(async () => page.evaluate(async () => {
    return await new Promise<boolean>((resolve) => {
      const open = indexedDB.open('studgroup-academic-cache-v1');
      open.onerror = () => resolve(false);
      open.onsuccess = () => {
        const db = open.result;
        if (!db.objectStoreNames.contains('private')) { db.close(); resolve(false); return; }
        const req = db.transaction('private', 'readonly').objectStore('private').get('snapshot');
        req.onsuccess = () => {
          const record = req.result as { ciphertext?: ArrayBuffer } | undefined;
          db.close();
          resolve(record?.ciphertext instanceof ArrayBuffer && record.ciphertext.byteLength > 0);
        };
        req.onerror = () => { db.close(); resolve(false); };
      };
    });
  }), { timeout: 10_000 }).toBe(true);

  // This scenario delays bootstrap and each fresh Today response by 2.5s.
  // The academic view should appear from the encrypted snapshot after auth,
  // without waiting an additional 2.5 seconds for the fresh /today response.
  // Return to the same group 25 minutes later. The encrypted snapshot is
  // now genuinely old, but it must not ask the student to refresh while an
  // automatic request for a fresh Today feed is still in flight.
  await page.clock.setFixedTime(new Date('2026-10-05T06:25:00Z'));
  await page.goto('/?scenario=slow');
  await expect(page.getByRole('heading', { name: 'Сегодня', level: 1 })).toBeVisible({ timeout: 7_000 });
  await expect(page.getByText('ДЗ №12–140').first()).toBeVisible({ timeout: 1_000 });
  await expect(page.getByText('Обновляем данные…')).toBeVisible();
  await expect(page.getByText(/Данные могли устареть/)).toHaveCount(0);
  await expect(page.getByText('Обновляем данные…')).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByText(/Данные могли устареть/)).toHaveCount(0);

  // A different authorization result must not reveal that student's cached data.
  await page.goto('/?scenario=no-group');
  await expect(page.getByText('Нет активной группы')).toBeVisible();
  await expect(page.getByText('ДЗ №12–140')).toHaveCount(0);
});
