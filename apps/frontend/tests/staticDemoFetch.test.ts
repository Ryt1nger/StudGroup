import { describe, expect, test } from 'vitest';
import type { HomeworkListResponse, SessionBootstrapResponse } from '@studgroup/shared-types';
import { createStaticDemoFetch } from '../src/mocks/staticDemoFetch';

const API = 'https://api.studgroup.example/v1';

describe('static mobile demo transport', () => {
  test('boots an anonymized session without a service worker', async () => {
    const response = await createStaticDemoFetch()(`${API}/session/bootstrap`, { method: 'POST' });
    const body = (await response.json()) as SessionBootstrapResponse;

    expect(response.status).toBe(200);
    expect(body.session.user.display_name).toBe('Демо');
    expect(body.session.group?.name).toBe('БИ 1.2 · обезличено');
  });

  test('serves reviewed homework through the generated API boundary', async () => {
    const response = await createStaticDemoFetch()(`${API}/homework?filter=all&limit=50`);
    const body = (await response.json()) as HomeworkListResponse;

    expect(response.status).toBe(200);
    expect(body.items.some((item) => item.title === 'Написать пять пар терминов-паронимов')).toBe(true);
    expect(body.next_cursor).toBeNull();
  });
});
