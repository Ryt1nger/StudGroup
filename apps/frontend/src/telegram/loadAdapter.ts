import type { TelegramAdapter } from './adapter';
import { createRealAdapter } from './realAdapter';

/**
 * Resolves the adapter. The mock path is statically unreachable in production builds:
 * `import.meta.env.DEV` is replaced by `false`, so Rollup drops the dynamic import.
 */
export async function loadTelegramAdapter(): Promise<TelegramAdapter | null> {
  const real = createRealAdapter();
  if (real) return real;

  const isDemo = import.meta.env.MODE === 'demo';

  if (import.meta.env.DEV && import.meta.env.VITE_LOCAL_PREVIEW === 'true') {
    const { createLocalPreviewAdapter } = await import('./localPreviewAdapter');
    return createLocalPreviewAdapter();
  }

  if (isDemo || (import.meta.env.DEV && import.meta.env.VITE_TELEGRAM_MOCK === 'true')) {
    const { createMockAdapter } = await import('./mockAdapter');
    const params = new URLSearchParams(window.location.search);
    const dark = params.get('theme') === 'dark' || (params.get('theme') === null && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
    return createMockAdapter({
      scheme: dark ? 'dark' : 'light',
      startParam: params.get('startapp'),
      hasInitData: params.get('scenario') !== 'outside-telegram',
    });
  }
  return null;
}
