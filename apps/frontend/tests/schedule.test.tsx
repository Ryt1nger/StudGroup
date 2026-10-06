import { screen } from '@testing-library/react';
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

  it('shows the week label from selected_week', async () => {
    useScenario();
    renderApp({ route: '/schedule' });
    expect(await screen.findByText(/Первая неделя/)).toBeInTheDocument();
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
    expect(await screen.findByText('Занятий на этой неделе нет')).toBeInTheDocument();
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
});
