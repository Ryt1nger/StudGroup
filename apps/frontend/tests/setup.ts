import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './helpers';

beforeAll(() => {
  server.events.on('request:unhandled', ({ request }) => {
    throw new Error(`Unhandled request in test: ${request.method} ${request.url}`);
  });
  server.listen();
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
