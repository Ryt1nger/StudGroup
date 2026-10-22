import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import { server } from './helpers';

// findBy*/waitFor give up after 1 s by default. On a busy machine the first render of a file (cold
// module load) takes longer, so the whole suite flaked; a missing element still fails, only later.
configure({ asyncUtilTimeout: 5000 });
beforeAll(() => {
  // JSDOM has no layout/scroll implementation; route behavior is asserted separately.
  vi.stubGlobal('scrollTo', vi.fn());
  Element.prototype.scrollIntoView = vi.fn();
  server.events.on('request:unhandled', ({ request }) => {
    throw new Error(`Unhandled request in test: ${request.method} ${request.url}`);
  });
  server.listen();
});
// Fixtures describe "Monday 2026-10-05 09:00 Moscow". Pin only the wall clock (timers stay real) so
// the suite does not depend on the day it is run; a test may install its own fake timers.
const FIXTURE_NOW = new Date('2026-10-05T06:00:00Z');
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(FIXTURE_NOW);
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
  document.cookie = 'studgroup_theme=; Path=/; Max-Age=0';
  server.resetHandlers();
});
afterAll(() => server.close());
