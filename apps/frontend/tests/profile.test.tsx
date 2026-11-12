import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { demoProfile } from '../src/mocks/profile';
import { API, renderApp, server, useScenario } from './helpers';

describe('profile', () => {
  it('blocks credential entry while operator documents are unapproved', async () => {
    useScenario();
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(demoProfile())),
      http.get(`${API}/legal/status`, () => HttpResponse.json({
        version: '2026-11-09-v2', ready: false, credentials_ready: false, terms: false, service: false, sdo: false,
      })),
    );
    renderApp({ route: '/profile/sdo' });
    expect(await screen.findByText(/Сохранение пока недоступно/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Логин СДО / ЛК')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Пароль СДО / ЛК')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Сохранить данные СДО / ЛК' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Подключение пока недоступно' })).toBeInTheDocument();
  });

  it('blocks credential entry when documents are approved but encryption is unavailable', async () => {
    useScenario();
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(demoProfile())),
      http.get(`${API}/legal/status`, () => HttpResponse.json({
        version: '2026-11-09-v2', ready: true, credentials_ready: false,
        terms: true, service: true, sdo: false,
      })),
    );
    renderApp({ route: '/profile/sdo' });
    expect(await screen.findByText(/не настроен ключ шифрования/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Логин СДО / ЛК')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Пароль СДО / ЛК')).not.toBeInTheDocument();
  });

  it('requires three separate legal consents before enabling SDO save', async () => {
    useScenario();
    const pending = { version: '2026-11-09-v2', ready: true, credentials_ready: true, terms: false, service: false, sdo: false };
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(demoProfile())),
      http.get(`${API}/legal/status`, () => HttpResponse.json(pending)),
    );
    renderApp({ route: '/profile/sdo' });
    const login = await screen.findByLabelText('Логин СДО / ЛК');
    await waitFor(() => expect(login).toBeEnabled());
    expect(screen.getByRole('heading', { name: 'Подключить СДО' })).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Сохранить данные СДО / ЛК' });
    expect(save).toBeDisabled();
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);
    await userEvent.click(screen.getByLabelText('Принимаю условия использования'));
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByLabelText('Согласен на обработку данных в StudGroup'));
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByLabelText('Добровольно разрешаю хранить логин и пароль СДО/ЛК'));
    expect(save).toBeEnabled();
  });

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

  it('sends only username before @ when SDO login is entered as email', async () => {
    useScenario();
    const sent: Array<Record<string, unknown>> = [];
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(demoProfile())),
      http.get(`${API}/legal/status`, () => HttpResponse.json({
        version: '2026-11-09-v2', ready: true, credentials_ready: true,
        terms: true, service: true, sdo: true,
      })),
      http.patch(`${API}/profile`, async ({ request }) => {
        sent.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({
          ...demoProfile(), sdu: { ...demoProfile().sdu, login: 'ivan', password_set: true },
        });
      }),
    );
    renderApp({ route: '/profile/sdo' });
    const login = await screen.findByLabelText('Логин СДО / ЛК');
    await waitFor(() => expect(login).toBeEnabled());
    await userEvent.clear(login);
    await userEvent.type(login, 'ivan@ranepa.ru');
    await userEvent.type(screen.getByLabelText('Пароль СДО / ЛК'), 'test-password');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить данные СДО / ЛК' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ sdu_login: 'ivan', sdu_password: 'test-password' });
    expect(login).toHaveValue('ivan');
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
