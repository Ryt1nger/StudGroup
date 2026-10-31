import { client, type MaterialLink } from '@studgroup/shared-types';
import { apiAuthorization } from '../../api/client';

export type CardKind = 'homework' | 'deadline';
export type MaterialResult = { revision: number; materials: MaterialLink[] };

export async function changeMaterial(
  kind: CardKind,
  cardId: string,
  revision: number,
  operation: 'add' | 'update' | 'remove' | 'upload',
  options: { id?: string; title?: string; url?: string; file?: File } = {},
): Promise<MaterialResult> {
  const base = client.getConfig().baseUrl ?? '';
  const path = `/v1/cards/${kind}/${encodeURIComponent(cardId)}/materials`;
  const url = new URL(
    `${base ? base.replace(/\/v1\/?$/, '') : window.location.origin}${path}${operation === 'update' || operation === 'remove' ? `/${options.id}` : operation === 'upload' ? '/file' : ''}`,
  );
  if (operation === 'remove') url.searchParams.set('expected_revision', String(revision));
  let body: BodyInit | undefined;
  if (operation === 'upload') {
    const form = new FormData();
    form.set('expected_revision', String(revision));
    form.set('file', options.file!);
    if (options.id) form.set('replace_id', options.id);
    body = form;
  } else if (operation !== 'remove')
    body = JSON.stringify({ expected_revision: revision, title: options.title, url: options.url });
  const response = await fetch(url, {
    method: { add: 'POST', update: 'PATCH', remove: 'DELETE', upload: 'POST' }[operation],
    headers: {
      ...(apiAuthorization() ? { Authorization: apiAuthorization()! } : {}),
      ...(operation === 'upload' || operation === 'remove' ? {} : { 'Content-Type': 'application/json' }),
    },
    body,
    credentials: 'omit',
  });
  if (!response.ok) {
    const result = (await response.json().catch(() => null)) as {
      error?: { message?: string };
      message?: string;
    } | null;
    throw new Error(result?.error?.message ?? result?.message ?? `Ошибка загрузки (${response.status})`);
  }
  return response.json() as Promise<MaterialResult>;
}
