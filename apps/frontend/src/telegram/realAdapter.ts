import type { ColorScheme, SafeArea, TelegramAdapter } from './adapter';

/** Returns an adapter around the official bridge, or null when the page is not inside Telegram. */
export function createRealAdapter(): TelegramAdapter | null {
  const app = window.Telegram?.WebApp;
  // The bridge script is always loaded; outside Telegram it exists but has empty initData.
  if (!app || !app.initData) return null;

  const scheme = (): ColorScheme => (app.colorScheme === 'dark' ? 'dark' : 'light');

  return {
    isTelegram: true,
    isMock: false,
    initData: app.initData,
    startParam: app.initDataUnsafe?.start_param ?? null,
    platform: app.platform,
    supportsBackButton: app.isVersionAtLeast('6.1'),
    get colorScheme() {
      return scheme();
    },
    ready() {
      app.ready();
      app.expand();
    },
    onThemeChange(listener) {
      const handler = () => listener(scheme());
      app.onEvent('themeChanged', handler);
      return () => app.offEvent('themeChanged', handler);
    },
    safeArea(): SafeArea {
      const safe = app.safeAreaInset ?? { top: 0, right: 0, bottom: 0, left: 0 };
      const content = app.contentSafeAreaInset ?? { top: 0, right: 0, bottom: 0, left: 0 };
      return {
        top: safe.top + content.top,
        right: safe.right + content.right,
        bottom: safe.bottom + content.bottom,
        left: safe.left + content.left,
      };
    },
    onViewportChange(listener) {
      const events = ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged'];
      events.forEach((event) => app.onEvent(event, listener));
      return () => events.forEach((event) => app.offEvent(event, listener));
    },
    showBackButton(onClick) {
      if (!app.isVersionAtLeast('6.1')) return null;
      app.BackButton.onClick(onClick);
      app.BackButton.show();
      return () => {
        app.BackButton.offClick(onClick);
        app.BackButton.hide();
      };
    },
    openTelegramLink(url) {
      app.openTelegramLink(url);
    },
    haptic(kind) {
      if (!app.isVersionAtLeast('6.1') || !app.HapticFeedback) return;
      if (kind === 'light') app.HapticFeedback.impactOccurred('light');
      else app.HapticFeedback.notificationOccurred(kind);
    },
  };
}
