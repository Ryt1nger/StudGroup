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

/** Multipart uploads use the same in-memory session as the generated JSON client. */
export function apiAuthorization(): string | null {
  return accessToken ? `Bearer ${accessToken}` : null;
}

/** Keep the OpenAPI server prefix even when Render supplies only the API origin.
 * The generated SDK requests /session/bootstrap, while FastAPI mounts it at /v1/session/bootstrap.
 */
export function normalizeApiBaseUrl(configured: string | undefined): string | undefined {
  const cleaned = configured?.trim().replace(/\/+$/, '');
  if (!cleaned) return undefined;
  return cleaned.endsWith('/v1') ? cleaned : `${cleaned}/v1`;
}

export function configureApiClient(fetchImplementation?: typeof fetch): void {
  const baseUrl = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);
  client.setConfig({
    ...(baseUrl ? { baseUrl } : {}),
    ...(fetchImplementation ? { fetch: fetchImplementation } : {}),
    auth: () => accessToken ?? undefined,
    credentials: 'omit', // contract: browser credentials are disabled
  });
}
