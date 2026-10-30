import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './helpers';

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

// jsdom has no IntersectionObserver: a controllable stand-in lets tests "scroll to the end" of a list.
type Observer = { callback: IntersectionObserverCallback; nodes: Set<Element> };
const observers = new Set<Observer>();
class FakeIntersectionObserver {
  private readonly entry: Observer;
  constructor(callback: IntersectionObserverCallback) {
    this.entry = { callback, nodes: new Set() };
    observers.add(this.entry);
  }
  observe(node: Element) { this.entry.nodes.add(node); }
  unobserve(node: Element) { this.entry.nodes.delete(node); }
  disconnect() { observers.delete(this.entry); }
  takeRecords() { return []; }
}
Object.defineProperty(globalThis, 'IntersectionObserver', { value: FakeIntersectionObserver, configurable: true, writable: true });
(globalThis as Record<string, unknown>).__reachEnd = (): number => {
  let fired = 0;
  for (const entry of [...observers]) {
    const records = [...entry.nodes].map((target) => ({ isIntersecting: true, target }) as IntersectionObserverEntry);
    if (records.length) {
      entry.callback(records, {} as IntersectionObserver);
      fired += 1;
    }
  }
  return fired;
};
