import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { ColorScheme, TelegramAdapter } from './adapter';

interface TelegramContextValue {
  adapter: TelegramAdapter | null;
  scheme: ColorScheme;
  /** `preference`: the user's choice; `setTheme(null)` goes back to following Telegram. */
  preference: ColorScheme | null;
  setTheme: (scheme: ColorScheme | null) => void;
  accent: Accent;
  setAccent: (accent: Accent) => void;
}

export const ACCENTS = ['violet', 'blue', 'green', 'red', 'yellow', 'gray'] as const;
export type Accent = (typeof ACCENTS)[number];

const TelegramContext = createContext<TelegramContextValue | null>(null);

// Only a non-sensitive appearance preference; never store learning data or credentials.
const THEME_COOKIE = 'studgroup_theme';
function savedTheme(): ColorScheme | null {
  try {
    const value = document.cookie.split('; ').find((part) => part.startsWith(`${THEME_COOKIE}=`))?.split('=')[1];
    return value === 'dark' || value === 'light' ? value : null;
  } catch { return null; }
}

const ACCENT_COOKIE = 'studgroup_accent';
function savedAccent(): Accent {
  try {
    const value = document.cookie.split('; ').find((part) => part.startsWith(`${ACCENT_COOKIE}=`))?.split('=')[1];
    return ACCENTS.find((accent) => accent === value) ?? 'violet';
  } catch { return 'violet'; }
}

export function TelegramProvider({ adapter, children }: { adapter: TelegramAdapter | null; children: ReactNode }) {
  const subscribe = useCallback((notify: () => void) => adapter?.onThemeChange(notify) ?? (() => undefined), [adapter]);
  const telegramScheme = useSyncExternalStore<ColorScheme>(subscribe, () => adapter?.colorScheme ?? 'light', () => 'light');
  const [preferred, setPreferred] = useState<ColorScheme | null>(savedTheme);
  const scheme = preferred ?? telegramScheme;
  const setTheme = useCallback((next: ColorScheme | null) => {
    setPreferred(next);
    try {
      document.cookie = `${THEME_COOKIE}=${next ?? ''}; Path=/; Max-Age=${next ? 31536000 : 0}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
    } catch { /* Appearance still works when persistent cookies are blocked. */ }
  }, []);

  const [accent, setAccentState] = useState<Accent>(savedAccent);
  const setAccent = useCallback((next: Accent) => {
    setAccentState(next);
    try {
      document.cookie = `${ACCENT_COOKIE}=${next}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
    } catch { /* The accent still applies for this visit when persistent cookies are blocked. */ }
  }, []);
  useEffect(() => {
    document.documentElement.dataset.accent = accent;
  }, [accent]);

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

  const value = useMemo(() => ({ adapter, scheme, preference: preferred, setTheme, accent, setAccent }), [adapter, scheme, preferred, setTheme, accent, setAccent]);
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
