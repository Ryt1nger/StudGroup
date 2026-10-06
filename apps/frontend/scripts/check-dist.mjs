// Fails the production build if development-only code or storage APIs leaked into dist/.
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const dist = new URL('../dist', import.meta.url).pathname;
const forbidden = [
  ['local-real-data-preview', 'local preview adapter'],
  ['Реальные данные · тестовый импорт', 'local preview banner'],
  ['__STUDGROUP_TELEGRAM_MOCK__', 'Telegram mock adapter marker'],
  ['mock=1&hash=invalid-development-only', 'mock initData'],
  ['mockServiceWorker', 'MSW service worker reference'],
  ['/preview/schedule', 'dev-only schedule preview route'],
  ['DemoHeaderActions', 'mock-only header actions'],
  ['реальные уведомления пока недоступны', 'mock-only demo notifications'],
  ['Реферат по социологии', 'mock data'],
  ['tasks-cursor-expired', 'mock scenario names'],
  ['localStorage', 'localStorage usage'],
  ['indexedDB', 'IndexedDB usage'],
];
function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
if (!existsSync(dist)) throw new Error('dist/ not found');
const files = walk(dist);
const problems = [];
if (files.some((f) => f.endsWith('mockServiceWorker.js'))) problems.push('mockServiceWorker.js is present in dist/');
for (const file of files.filter((f) => /\.(js|html|css|json)$/.test(f))) {
  const text = readFileSync(file, 'utf8');
  for (const [needle, label] of forbidden) if (text.includes(needle)) problems.push(`${label} found in ${file.replace(dist, 'dist')}`);
}
// React Router keeps a list of *applied view-transition route keys* in sessionStorage (library internal,
// no learning data). Any other sessionStorage use fails the build; app code is also blocked by ESLint.
for (const file of files.filter((f) => f.endsWith('.js'))) {
  const text = readFileSync(file, 'utf8');
  const uses = text.split('sessionStorage').length - 1;
  const routerOnly = text.includes('applied view transitions') && uses <= 3;
  if (uses > 0 && !routerOnly) problems.push(`sessionStorage usage found in ${file.replace(dist, 'dist')}`);
}
if (problems.length) {
  console.error('check-dist failed:\n- ' + problems.join('\n- '));
  process.exit(1);
}
console.log(`check-dist ok (${files.length} files)`);
