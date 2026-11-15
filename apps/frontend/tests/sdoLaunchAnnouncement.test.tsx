import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { demoProfile } from '../src/mocks/profile';
import { API, renderApp, server, useScenario } from './helpers';

describe('one-time SDO launch announcement', () => {
  it('opens only when the server grants the first-visit claim, then closes permanently', async () => {
    useScenario();
    let claims = 0;
    server.use(
      http.post(`${API}/sdo-launch-prompt/claim`, () => {
        claims += 1;
        return HttpResponse.json({ show: claims === 1 });
      }),
    );
    renderApp();
    const dialog = await screen.findByRole('dialog', { name: 'Расписание и СДО — в StudGroup' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByText('Подключение добровольное. Данные защищены.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Позже' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Сегодня', level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(claims).toBe(1));
    // The server, not browser storage, remembers this user's one-time receipt.
    // eslint-disable-next-line no-restricted-properties -- contract: no client persistence
    expect(window.localStorage.length).toBe(0);
  });

  it('links to the existing credential and consent screen', async () => {
    useScenario();
    server.use(http.post(`${API}/sdo-launch-prompt/claim`, () => HttpResponse.json({ show: true })));
    renderApp();
    await screen.findByRole('dialog', { name: 'Расписание и СДО — в StudGroup' });
    await userEvent.click(screen.getByRole('button', { name: 'Подключить СДО / ЛК' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Подключить СДО' })).toBeInTheDocument();
  });

  it('never claims or shows the announcement to an already connected user', async () => {
    useScenario();
    const profile = demoProfile();
    profile.sdu.password_set = true;
    let claims = 0;
    let profileReads = 0;
    server.use(
      http.get(`${API}/profile`, () => {
        profileReads += 1;
        return HttpResponse.json(profile);
      }),
      http.post(`${API}/sdo-launch-prompt/claim`, () => {
        claims += 1;
        return HttpResponse.json({ show: true });
      }),
    );
    renderApp();
    await waitFor(() => expect(profileReads).toBeGreaterThan(0));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(claims).toBe(0);
  });

  it('does not show before rollout when server returns show=false', async () => {
    useScenario();
    let claims = 0;
    server.use(
      http.post(`${API}/sdo-launch-prompt/claim`, () => {
        claims += 1;
        return HttpResponse.json({ show: false });
      }),
    );
    renderApp();
    await waitFor(() => expect(claims).toBe(1));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
