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

export function receiveSupportSession(): Promise<SupportSession> {
  if (received) return received;
  received = new Promise((resolve, reject) => {
    const api = import.meta.env.VITE_API_BASE_URL;
    if (!api || !window.opener) {
      reject(new Error('support_unavailable'));
      return;
    }
    const origin = new URL(api, window.location.origin).origin;
    const opener = window.opener as Window;
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', listener);
      reject(new Error('support_timeout'));
    }, 15000);
    function listener(event: MessageEvent) {
      if (event.origin !== origin || event.source !== opener || event.data?.type !== 'studgroup-support-session') return;
      const result = event.data.result as SupportSession;
      if (!result?.access_token?.startsWith('sgsupport1.') || !['view', 'edit'].includes(result.mode)) return;
      window.clearTimeout(timer);
      window.removeEventListener('message', listener);
      supportMode = result.mode;
      resolve(result);
    }
    window.addEventListener('message', listener);
    opener.postMessage({ type: 'studgroup-support-ready' }, origin);
  });
  return received;
}

export function exitSupport() {
  window.close();
  const api = import.meta.env.VITE_API_BASE_URL;
  if (api) window.location.replace(new URL('/admin/#audience', api).href);
}
