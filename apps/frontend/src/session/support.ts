import type { SessionContext } from '@studgroup/shared-types';

type SupportSession = {
  access_token: string;
  expires_at: string;
  mode: 'view' | 'edit';
  session: SessionContext;
};

export const supportRequested = new URLSearchParams(window.location.search).get('support') === '1';
let received: Promise<SupportSession> | null = null;
export let supportMode: SupportSession['mode'] | null = null;

function supportHost(): { peer: Window; origin: string } | null {
  const api = import.meta.env.VITE_API_BASE_URL;
  if (!api || window.parent === window) return null;
  return { peer: window.parent, origin: new URL(api, window.location.origin).origin };
}

/** Never accept a capability from a different origin or an unrelated frame. */
export function isSupportMessage(event: MessageEvent, peer: Window, origin: string): boolean {
  return event.source === peer && event.origin === origin && event.data?.type === 'studgroup-support-session';
}

export function receiveSupportSession(): Promise<SupportSession> {
  if (received) return received;
  received = new Promise<SupportSession>((resolve, reject) => {
    const host = supportHost();
    if (!host) {
      reject(new Error('support_unavailable'));
      return;
    }
    const { peer, origin } = host;
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', listener);
      reject(new Error('support_timeout'));
    }, 15000);
    function listener(event: MessageEvent) {
      if (!isSupportMessage(event, peer, origin)) return;
      const result = event.data.result as SupportSession;
      if (!result?.access_token?.startsWith('sgsupport1.') || !['view', 'edit'].includes(result.mode)) return;
      window.clearTimeout(timer);
      window.removeEventListener('message', listener);
      supportMode = result.mode;
      resolve(result);
    }
    window.addEventListener('message', listener);
    peer.postMessage({ type: 'studgroup-support-ready' }, origin);
  }).catch((error: unknown) => {
    received = null;
    throw error;
  });
  return received;
}

export function exitSupport() {
  const host = supportHost();
  if (host) {
    host.peer.postMessage({ type: 'studgroup-support-exit' }, host.origin);
    return;
  }
  const api = import.meta.env.VITE_API_BASE_URL;
  if (api) window.location.replace(new URL('/admin/#audience', api).href);
}
