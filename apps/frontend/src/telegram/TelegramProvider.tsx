import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { ColorScheme, TelegramAdapter } from './adapter';

interface TelegramContextValue {
  adapter: TelegramAdapter | null;
  scheme: ColorScheme;
  setTheme: (scheme: ColorScheme) => void;
}

const TelegramContext = createContext<TelegramContextValue | null>(null);

// Only a non-sensitive appearance preference; never store learning data or credentials.
const THEME_COOKIE = 'studgroup_theme';
function savedTheme(): ColorScheme | null {
  try {
    const value = document.cookie.split('; ').find((part) => part.startsWith(`${THEME_COOKIE}=`))?.split('=')[1];
    return value === 'dark' || value === 'light' ? value : null;
  } catch { return null; }
}

export function TelegramProvider({ adapter, children }: { adapter: TelegramAdapter | null; children: ReactNode }) {
  const subscribe = useCallback((notify: () => void) => adapter?.onThemeChange(notify) ?? (() => undefined), [adapter]);
  const telegramScheme = useSyncExternalStore<ColorScheme>(subscribe, () => adapter?.colorScheme ?? 'light', () => 'light');
  const [preferred, setPreferred] = useState<ColorScheme | null>(savedTheme);
  const scheme = preferred ?? telegramScheme;
  const setTheme = useCallback((next: ColorScheme) => {
    setPreferred(next);
    try {
      document.cookie = `${THEME_COOKIE}=${next}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
    } catch { /* Appearance still works when persistent cookies are blocked. */ }
  }, []);

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

  const value = useMemo(() => ({ adapter, scheme, setTheme }), [adapter, scheme, setTheme]);
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
