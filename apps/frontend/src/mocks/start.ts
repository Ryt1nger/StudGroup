import { readScenario } from './scenarios';
import { createHandlers } from './handlers';

/**
 * Starts the in-browser MSW worker. Called only behind development flags or the explicit
 * static demo mode, via a dynamic import, so normal production bundles never contain it.
 */
export async function startApiMock(baseUrl: string): Promise<void> {
  const { setupWorker } = await import('msw/browser');
  const worker = setupWorker(...createHandlers(baseUrl, readScenario(window.location.search)));
  await worker.start({
    quiet: true,
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
  });
}
