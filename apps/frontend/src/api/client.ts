import { client } from '@studgroup/shared-types';

/**
 * The bearer token lives in module memory only (contract: never in storage, URLs or logs).
 */
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

/** Private endpoints which have not yet become part of the public student contract. */
export async function ownerRequest(path: string, init: RequestInit = {}) {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${accessToken ?? ''}`, 'Content-Type': 'application/json', ...init.headers },
    credentials: 'omit',
  });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error?.message ?? 'Не удалось загрузить preview');
  return response.json();
}

export function configureApiClient(fetchImplementation?: typeof fetch): void {
  const baseUrl = import.meta.env.VITE_API_BASE_URL;
  client.setConfig({
    ...(baseUrl ? { baseUrl } : {}),
    ...(fetchImplementation ? { fetch: fetchImplementation } : {}),
    auth: () => accessToken ?? undefined,
    credentials: 'omit', // contract: browser credentials are disabled
  });
}
