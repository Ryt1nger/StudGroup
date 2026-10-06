/**
 * Development/test-only Telegram adapter. It is loaded with a dynamic import behind
 * `import.meta.env.DEV`, so Vite removes it from production bundles; scripts/check-dist.mjs
 * fails the build if the marker below ever appears in `dist/`.
 */
import type { ColorScheme, TelegramAdapter } from './adapter';

export const MOCK_ADAPTER_MARKER = '__STUDGROUP_TELEGRAM_MOCK__';

export interface MockOptions {
  scheme: ColorScheme;
  startParam: string | null;
  /** When false the mock behaves like a browser opened outside Telegram. */
  hasInitData: boolean;
}

export function createMockAdapter(options: MockOptions): TelegramAdapter {
  let scheme = options.scheme;
  const themeListeners = new Set<(s: ColorScheme) => void>();
  (globalThis as Record<string, unknown>)[MOCK_ADAPTER_MARKER] = {
    setScheme(next: ColorScheme) {
      scheme = next;
      themeListeners.forEach((l) => l(next));
    },
  };
  return {
    isTelegram: false,
    isMock: true,
    // Deliberately invalid: the real backend must reject it.
    initData: options.hasInitData ? 'mock=1&hash=invalid-development-only' : '',
    startParam: options.startParam,
    platform: 'mock',
    supportsBackButton: false,
    get colorScheme() {
      return scheme;
    },
    ready() {},
    onThemeChange(listener) {
      themeListeners.add(listener);
      return () => themeListeners.delete(listener);
    },
    safeArea: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
    onViewportChange: () => () => {},
    // Browsers have no native Telegram back button; the shell renders a fallback in mock mode.
    showBackButton: () => null,
    openTelegramLink(url) {
      console.info('[mock telegram] openTelegramLink', url);
      (globalThis as Record<string, unknown>).__lastTelegramLink = url;
    },
    haptic() {},
  };
}
