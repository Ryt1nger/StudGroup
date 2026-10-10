/** DEV-only browser shell. Learning data comes from the real isolated local backend. */
import type { ColorScheme, TelegramAdapter } from './adapter';

export function createLocalPreviewAdapter(): TelegramAdapter {
  const params = new URLSearchParams(window.location.search);
  const dark =
    params.get('theme') === 'dark' ||
    (params.get('theme') === null && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
  let scheme: ColorScheme = dark ? 'dark' : 'light';
  const listeners = new Set<(scheme: ColorScheme) => void>();
  return {
    isTelegram: false,
    isMock: false,
    initData: import.meta.env.VITE_LOCAL_PREVIEW_CREDENTIAL ?? '',
    startParam: null,
    platform: 'local-real-data-preview',
    supportsBackButton: false,
    get colorScheme() {
      return scheme;
    },
    setColorScheme(next) {
      scheme = next;
      listeners.forEach((listener) => listener(next));
    },
    ready() {},
    onThemeChange(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    safeArea: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
    onViewportChange: () => () => {},
    showBackButton: () => null,
    openTelegramLink(url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    openExternalLink(url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    haptic() {},
  };
}
