import type { ColorScheme, TelegramAdapter } from './adapter';

/**
 * Adds demo-only appearance controls without replacing Telegram's native bridge.
 * Navigation, safe-area, links and haptics continue to use the real adapter.
 */
export function createDemoAdapter(base: TelegramAdapter): TelegramAdapter {
  let selectedScheme: ColorScheme | null = null;
  const listeners = new Set<(scheme: ColorScheme) => void>();
  const currentScheme = () => selectedScheme ?? base.colorScheme;

  return {
    ...base,
    get colorScheme() {
      return currentScheme();
    },
    setColorScheme(next) {
      if (next === currentScheme()) return;
      selectedScheme = next;
      listeners.forEach((listener) => listener(next));
    },
    onThemeChange(listener) {
      listeners.add(listener);
      const unsubscribe = base.onThemeChange(() => listener(currentScheme()));
      return () => {
        listeners.delete(listener);
        unsubscribe();
      };
    },
  };
}
