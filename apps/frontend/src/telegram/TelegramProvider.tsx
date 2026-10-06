import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react';
import type { ColorScheme, TelegramAdapter } from './adapter';

interface TelegramContextValue {
  adapter: TelegramAdapter | null;
  scheme: ColorScheme;
}

const TelegramContext = createContext<TelegramContextValue | null>(null);

export function TelegramProvider({ adapter, children }: { adapter: TelegramAdapter | null; children: ReactNode }) {
  const subscribe = useCallback((notify: () => void) => adapter?.onThemeChange(notify) ?? (() => undefined), [adapter]);
  const scheme = useSyncExternalStore<ColorScheme>(subscribe, () => adapter?.colorScheme ?? 'light', () => 'light');

  useEffect(() => {
    adapter?.ready();
  }, [adapter]);

  useEffect(() => {
    document.documentElement.dataset.theme = scheme;
  }, [scheme]);

  useEffect(() => {
    if (!adapter) return;
    const apply = () => {
      const area = adapter.safeArea();
      const style = document.documentElement.style;
      style.setProperty('--sg-safe-top', `${area.top}px`);
      style.setProperty('--sg-safe-right', `${area.right}px`);
      style.setProperty('--sg-safe-bottom', `${area.bottom}px`);
      style.setProperty('--sg-safe-left', `${area.left}px`);
    };
    apply();
    return adapter.onViewportChange(apply);
  }, [adapter]);

  const value = useMemo(() => ({ adapter, scheme }), [adapter, scheme]);
  return <TelegramContext.Provider value={value}>{children}</TelegramContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTelegram(): TelegramContextValue {
  const value = useContext(TelegramContext);
  if (!value) throw new Error('useTelegram must be used inside <TelegramProvider>');
  return value;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ColorScheme {
  return useTelegram().scheme;
}
