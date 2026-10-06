/// <reference types="vitest/config" />
import { createReadStream, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

const require = createRequire(import.meta.url);

/**
 * Serves the MSW service worker only from the dev server. It is intentionally not in
 * `public/`, so a production bundle can never contain it (see scripts/check-dist.mjs).
 */
function devMockServiceWorker(): Plugin {
  return {
    name: 'studgroup-dev-msw-worker',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/mockServiceWorker.js', (_req, res) => {
        const file = path.resolve(path.dirname(require.resolve('msw')), '../mockServiceWorker.js');
        if (!existsSync(file)) {
          res.statusCode = 404;
          res.end();
          return;
        }
        res.setHeader('Content-Type', 'text/javascript');
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  base: mode === 'demo' ? '/StudGroup/' : '/',
  plugins: [react(), devMockServiceWorker()],
  server: { host: true, port: 5173 },
  preview: { host: true, port: 4173 },
  build: { target: 'es2022', sourcemap: false },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx}'],
    css: { modules: { classNameStrategy: 'non-scoped' } },
  },
}));
