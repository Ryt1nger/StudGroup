import type { SessionContext } from '@studgroup/shared-types';
import type { TelegramAdapter } from '../telegram/adapter';

type SupportSession = {
  access_token: string;
  expires_at: string;
  mode: 'view' | 'edit';
  session: SessionContext;
};

export const supportRequested = new URLSearchParams(window.location.search).get('support') === '1';
let received: Promise<SupportSession> | null = null;
export let supportMode: SupportSession['mode'] | null = null;

function adminOrigin(): string | null {
  const api = import.meta.env.VITE_API_BASE_URL;
  return api ? new URL(api, window.location.origin).origin : null;
}

export function requestSupportSession(host: Window, origin: string): Promise<SupportSession> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', listener);
      reject(new Error('support_timeout'));
    }, 15000);
    function listener(event: MessageEvent) {
      if (event.origin !== origin || event.source !== host || event.data?.type !== 'studgroup-support-session') return;
      const result = event.data.result as SupportSession;
      if (!result?.access_token?.startsWith('sgsupport1.') || !['view', 'edit'].includes(result.mode)) return;
      window.clearTimeout(timer);
      window.removeEventListener('message', listener);
      supportMode = result.mode;
      resolve(result);
    }
    window.addEventListener('message', listener);
    host.postMessage({ type: 'studgroup-support-ready' }, origin);
  });
}

export function receiveSupportSession(): Promise<SupportSession> {
  if (received) return received;
  const origin = adminOrigin();
  if (!origin || window.parent === window) return Promise.reject(new Error('support_unavailable'));
  received = requestSupportSession(window.parent, origin).catch((error: unknown) => {
    received = null;
    throw error;
  });
  return received;
}

export function exitSupport() {
  const origin = adminOrigin();
  if (origin && window.parent !== window) {
    window.parent.postMessage({ type: 'studgroup-support-close' }, origin);
  }
}

/** The enclosing admin WebView owns the native Telegram bridge. Never impersonate initData. */
export function createSupportAdapter(): TelegramAdapter | null {
  const origin = adminOrigin();
  if (!origin || window.parent === window) return null;
  const host = window.parent;
  const link = (url: string, telegram: boolean) => host.postMessage({ type: 'studgroup-support-link', url, telegram }, origin);
  return {
    isTelegram: true,
    isMock: false,
    initData: '',
    startParam: null,
    platform: 'support',
    supportsBackButton: false,
    colorScheme: new URLSearchParams(window.location.search).get('theme') === 'dark' ? 'dark' : 'light',
    ready: () => undefined,
    onThemeChange: () => () => undefined,
    safeArea: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
    onViewportChange: () => () => undefined,
    showBackButton: () => null,
    openTelegramLink: (url) => link(url, true),
    openExternalLink: (url) => link(url, false),
    haptic: () => undefined,
  };
}
