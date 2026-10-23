import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { demoProfile } from '../src/mocks/profile';
import { API, renderApp, server, useScenario } from './helpers';

describe('profile', () => {
  it('saves name, contacts and study-platform data, sending the password only when it was typed', async () => {
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
    await userEvent.type(screen.getByLabelText('Ссылка на СДО'), 'http://sdo.example.edu');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Нужна ссылка, которая начинается с https://')).toBeInTheDocument();
    expect(sent).toHaveLength(0);
    await userEvent.clear(screen.getByLabelText('Ссылка на СДО'));
    await userEvent.type(screen.getByLabelText('Ссылка на СДО'), 'https://sdo.example.edu');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ first_name: 'Тимофей', sdu_url: 'https://sdo.example.edu' });
    expect(sent[0]).not.toHaveProperty('sdu_password');
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
