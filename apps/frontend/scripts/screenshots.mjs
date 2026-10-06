// Captures reference screenshots against the dev server running the contract-example mocks.
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir]
import { mkdirSync } from 'node:fs';
import { chromium } from '@playwright/test';

const base = process.argv[2] ?? 'http://localhost:5173';
const out = process.argv[3] ?? 'screenshots';
mkdirSync(out, { recursive: true });

const exe = process.env.CHROMIUM_PATH || undefined;
const browser = await chromium.launch({ executablePath: exe });

const HW_TODAY = '44444444-4444-4444-8444-444444444444';
const HW_UNAVAILABLE = '66666666-6666-4666-8666-666666666666';
const HW_OVERDUE = '88888888-8888-4888-8888-888888888888';

const shots = [
  ['today', '/today', {}],
  ['today-empty', '/today', { scenario: 'empty' }],
  ['today-next-current', '/today', { scenario: 'next-current' }],
  ['today-next-null', '/today', { scenario: 'next-null' }],
  ['today-delayed', '/today', { scenario: 'delayed' }],
  ['today-stale', '/today', { scenario: 'stale' }],
  ['detail', `/homework/${HW_TODAY}`, {}],
  ['detail-source-unavailable', `/homework/${HW_UNAVAILABLE}`, {}],
  ['detail-overdue', `/homework/${HW_OVERDUE}`, {}],
  ['gate-no-group', '/today', { scenario: 'no-group' }],
  ['gate-suspended', '/today', { scenario: 'suspended' }],
  ['gate-expired', '/today', { scenario: 'expired' }],
  ['error-service', '/today', { scenario: 'service-error' }],
  ['schedule', '/schedule', {}],
  ['schedule-clarify', '/schedule', { scenario: 'schedule-clarify' }],
  ['schedule-empty', '/schedule', { scenario: 'schedule-empty' }],
  ['prepared-tasks', '/tasks', {}],
  ['preview-schedule', '/preview/schedule', {}],
];

for (const theme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: theme });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.error('pageerror', e.message));
  for (const [name, path, q] of shots) {
    const qs = new URLSearchParams({ theme, ...q }).toString();
    await page.goto(`${base}${path}?${qs}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${out}/${name}.${theme}.png`, fullPage: name.startsWith('detail') || name.startsWith('today') || name.startsWith('preview') });
  }
  // Interaction: mark done, then show failure state
  await page.goto(`${base}/homework/${HW_TODAY}?theme=${theme}`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Выполнено мной' }).click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${out}/detail-completed.${theme}.png` });
  await page.goto(`${base}/homework/${HW_TODAY}?theme=${theme}&scenario=completion-fails`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Выполнено мной' }).click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${out}/detail-completion-failed.${theme}.png` });
  await ctx.close();
}
await browser.close();
console.log('done');
