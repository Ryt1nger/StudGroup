import { copyFileSync } from 'node:fs';

const dist = new URL('../dist/', import.meta.url);
copyFileSync(new URL('index.html', dist), new URL('404.html', dist));
console.log('GitHub Pages SPA fallback created');
