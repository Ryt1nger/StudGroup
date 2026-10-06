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

export function configureApiClient(): void {
  const baseUrl = import.meta.env.VITE_API_BASE_URL;
  client.setConfig({
    ...(baseUrl ? { baseUrl } : {}),
    auth: () => accessToken ?? undefined,
    credentials: 'omit', // contract: browser credentials are disabled
  });
}
