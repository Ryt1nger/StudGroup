import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { demoProfile } from '../src/mocks/profile';
import { API, renderApp, server, useScenario } from './helpers';

describe('profile', () => {
  it('saves personal data and opens the separate, consent-gated SDO/LK form', async () => {
    useScenario();
    const sent: Array<Record<string, unknown>> = [];
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(demoProfile())),
      http.patch(`${API}/profile`, async ({ request }) => {
        sent.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ...demoProfile(), first_name: 'Тимофей' });
      }),
    );
    renderApp({ route: '/profile/me' });
    const first = await screen.findByLabelText('Имя');
    await userEvent.clear(first);
    await userEvent.type(first, 'Тимофей');
    expect(screen.getByRole('link', { name: /Логин и пароль СДО \/ ЛК/ })).toHaveAttribute('href', '/profile/sdo');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ first_name: 'Тимофей' });
    expect(sent[0]).not.toHaveProperty('sdu_password');
    expect(sent[0]).not.toHaveProperty('sdu_login');
  });

  it('writes a notification preference straight to the server', async () => {
    useScenario();
    let sent: { settings?: { enabled?: boolean } } = {};
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(demoProfile())),
      http.patch(`${API}/profile`, async ({ request }) => {
        sent = (await request.json()) as typeof sent;
        return HttpResponse.json(demoProfile());
      }),
    );
    renderApp({ route: '/profile/notify' });
    await userEvent.click(await screen.findByRole('switch', { name: 'Уведомления от бота' }));
    await waitFor(() => expect(sent.settings?.enabled).toBe(false));
  });
});
