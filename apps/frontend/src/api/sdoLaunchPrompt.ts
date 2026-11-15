/** One-time SDO feature announcement; only the server owns the receipt.
 *  Never store the account identifier, acknowledgement or auth token in
 *  localStorage, sessionStorage or a URL.
 */
import { client } from '@studgroup/shared-types';
import { apiAuthorization } from './client';

export async function claimSdoLaunchPrompt(): Promise<boolean> {
  const authorization = apiAuthorization();
  if (!authorization) return false;
  const base = (client.getConfig().baseUrl ?? '/v1').replace(/\/$/, '');
  const response = await fetch(`${base}/sdo-launch-prompt/claim`, {
    method: 'POST',
    cache: 'no-store',
    credentials: 'omit',
    headers: { Authorization: authorization },
  });
  if (!response.ok) throw new Error('SDO launch announcement status unavailable');
  const data: unknown = await response.json();
  return typeof data === 'object' && data !== null && 'show' in data && data.show === true;
}
