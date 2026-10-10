import { describe, expect, it, vi } from 'vitest';
import type { ColorScheme, TelegramAdapter } from '../src/telegram/adapter';
import { createDemoAdapter } from '../src/telegram/demoAdapter';

function realTelegramAdapter(initialScheme: ColorScheme) {
  let telegramScheme = initialScheme;
  const listeners = new Set<(scheme: ColorScheme) => void>();
  const base: TelegramAdapter = {
    isTelegram: true,
    isMock: false,
    initData: 'signed-telegram-data',
    startParam: 'homework-1',
    platform: 'android',
    supportsBackButton: true,
    get colorScheme() {
      return telegramScheme;
    },
    ready: vi.fn(),
    onThemeChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    safeArea: () => ({ top: 10, right: 0, bottom: 20, left: 0 }),
    onViewportChange: () => () => undefined,
    showBackButton: () => null,
    openTelegramLink: vi.fn(),
    openExternalLink: vi.fn(),
    haptic: vi.fn(),
  };

  return {
    base,
    emitTelegramTheme(next: ColorScheme) {
      telegramScheme = next;
      listeners.forEach((listener) => listener(next));
    },
  };
}

describe('demo Telegram adapter', () => {
  it('keeps Telegram capabilities while allowing a manual theme', () => {
    const telegram = realTelegramAdapter('light');
    const adapter = createDemoAdapter(telegram.base);
    const listener = vi.fn();
    adapter.onThemeChange(listener);

    expect(adapter.isTelegram).toBe(true);
    expect(adapter.platform).toBe('android');
    expect(adapter.safeArea()).toEqual({ top: 10, right: 0, bottom: 20, left: 0 });
    expect(adapter.colorScheme).toBe('light');

    adapter.setColorScheme?.('dark');
    expect(adapter.colorScheme).toBe('dark');
    expect(listener).toHaveBeenLastCalledWith('dark');

    telegram.emitTelegramTheme('light');
    expect(adapter.colorScheme).toBe('dark');
    expect(listener).toHaveBeenLastCalledWith('dark');
  });
});
