/** The legal API is deliberately outside generated profile types. No credential is logged or cached. */
import { client } from '@studgroup/shared-types';
import { apiAuthorization } from './client';

const legalBase = () => `${(client.getConfig().baseUrl ?? '/v1').replace(/\/$/, '')}/legal`;
export interface LegalStatus {
  version: string;
  ready: boolean;
  credentials_ready: boolean;
  terms: boolean;
  service: boolean;
  sdo: boolean;
}

async function legalRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const auth = apiAuthorization();
  const response = await fetch(`${legalBase()}${path}`, {
    ...options,
    credentials: 'omit',
    cache: 'no-store',
    headers: {
      ...(auth ? { Authorization: auth } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (!response.ok) {
    throw new Error(response.status === 503
      ? 'Документы оператора ещё не утверждены, подключение СДО/ЛК временно недоступно.'
      : 'Не удалось выполнить действие. Проверьте соединение и попробуйте снова.');
  }
  return response.json() as Promise<T>;
}

export function legalStatus(): Promise<LegalStatus> {
  return legalRequest('/status');
}

export function acceptLegal(purpose: 'terms' | 'service' | 'sdo', version: string): Promise<void> {
  return legalRequest('/consents', {
    method: 'POST',
    body: JSON.stringify({ purpose, version, confirmed: true }),
  }).then(() => undefined);
}

export function revokeSdoConsent(): Promise<void> {
  return legalRequest('/consents/sdo', { method: 'DELETE' }).then(() => undefined);
}

export async function legalDocument(document: string): Promise<string> {
  const slug = ['privacy', 'terms', 'service', 'sdo', 'cookies'].includes(document) ? document : null;
  if (!slug) throw new Error('Документ не найден');
  const response = await fetch(`${legalBase()}/documents/${slug}`, {
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!response.ok) throw new Error('Не удалось загрузить документ');
  return response.text();
}
