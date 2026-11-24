import { act, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, renderApp, server, useScenario } from './helpers';
import { weekRange } from '../src/lib/format';

describe('schedule (GET /v1/schedule)', () => {
  it('requests the current Monday–Sunday week in the group timezone and shows lessons', async () => {
    useScenario();
    let query: URLSearchParams | null = null;
    server.use(
      http.get(`${API}/schedule`, ({ request }) => {
        query = new URL(request.url).searchParams;
        return undefined; // fall through to the default mock handler
      }),
    );
    renderApp({ route: '/schedule' });
    expect((await screen.findAllByText('Математический анализ')).length).toBeGreaterThan(0);
    const expected = weekRange(Date.now(), 'Europe/Moscow');
    expect(query).not.toBeNull();
    expect(query!.get('start')).toBe(expected.start);
    expect(query!.get('end')).toBe(expected.end);
    expect(screen.queryByText('Раздел готовится')).not.toBeInTheDocument();
  });

  it('renders only contract fields: location and teacher, no invented lesson type or status badges', async () => {
    useScenario();
    renderApp({ route: '/schedule' });
    expect((await screen.findAllByText('Ауд. 320 · Иванов А. С.')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Подтверждено')).not.toBeInTheDocument();
    expect(screen.queryByText('Практика')).not.toBeInTheDocument();
  });

  it('explains unresolved parity (needs_clarification) instead of hiding it', async () => {
    useScenario('schedule-clarify');
    renderApp({ route: '/schedule' });
    expect(await screen.findByText(/Расписание уточняется/)).toBeInTheDocument();
    expect(screen.queryByText(/Первая неделя|Вторая неделя/)).not.toBeInTheDocument();
  });

  it('shows an empty state when the week has no lessons', async () => {
    useScenario('schedule-empty');
    renderApp({ route: '/schedule' });
    expect(await screen.findByText('Пар нет')).toBeInTheDocument();
  });

  it('shows a permission state on 403 and a retryable error on 5xx', async () => {
    useScenario('schedule-forbidden');
    const forbidden = renderApp({ route: '/schedule' });
    expect(await screen.findByText('Нет доступа')).toBeInTheDocument();
    forbidden.unmount();

    useScenario('schedule-error');
    renderApp({ route: '/schedule' });
    expect(await screen.findByText('Сервис временно недоступен', {}, { timeout: 6000 })).toBeInTheDocument();
  }, 15000);

  it('does not call the API without the schedule.read permission', async () => {
    useScenario();
    let calls = 0;
    server.use(
      http.get(`${API}/schedule`, () => {
        calls += 1;
        return HttpResponse.json({}, { status: 500 });
      }),
      http.post(`${API}/session/bootstrap`, async () => {
        const { exampleSession, sessionAt } = await import('../src/mocks/data');
        return HttpResponse.json({
          ...sessionAt(exampleSession, Date.now()),
          session: { ...sessionAt(exampleSession, Date.now()).session, permissions: ['today.read'] },
        });
      }),
    );
    renderApp({ route: '/schedule' });
    expect(await screen.findByText('Нет доступа к расписанию')).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it.each(['credentials_required', 'queued', 'running', 'cooldown'] as const)(
    'hides SDO maintenance details while the account state is %s and silently refreshes lessons',
    async (refreshState) => {
      useScenario();
      let scheduleCalls = 0;
      let refreshCalls = 0;
      let checked = '2026-11-22T19:10:22Z';
      const freshness = () => ({
        account_connected: true,
        oldest_required_checked_at: checked,
        oldest_links_checked_at: checked,
        last_success_at: checked,
        refresh_state: refreshState,
        weeks: [],
      });
      server.use(
        http.get(`${API}/ranepa-schedule`, () => HttpResponse.json(freshness())),
        http.get(`${API}/schedule`, () => {
          scheduleCalls += 1;
          return undefined;
        }),
        http.post(`${API}/ranepa-schedule/refresh`, () => {
          refreshCalls += 1;
          return HttpResponse.json({ state: 'queued', queued: true });
        }),
      );
      const app = renderApp({ route: '/schedule' });
      expect((await screen.findAllByText('Математический анализ')).length).toBeGreaterThan(0);
      await waitFor(() => expect(
        app.queryClient.getQueriesData({ queryKey: ['ranepa-freshness'] })[0]?.[1],
      ).toMatchObject({ account_connected: true, last_success_at: checked }));
      expect(screen.queryByRole('region', { name: 'Свежесть СДО' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Обновить СДО' })).not.toBeInTheDocument();
      expect(screen.queryByText(/Расписание СДО проверено|Ссылки СДО проверены|Требуется обновление|Не удалось войти в СДО|СДО обрабатывается в фоновом режиме|Заявка в очереди/)).not.toBeInTheDocument();

      const before = scheduleCalls;
      checked = '2026-11-24T06:00:00Z';
      await act(async () => {
        await app.queryClient.invalidateQueries({ queryKey: ['ranepa-freshness'] });
      });
      await waitFor(() => expect(scheduleCalls).toBeGreaterThan(before));
      expect(refreshCalls).toBe(0);
      expect(screen.queryByRole('region', { name: 'Свежесть СДО' })).not.toBeInTheDocument();
    },
  );
});
