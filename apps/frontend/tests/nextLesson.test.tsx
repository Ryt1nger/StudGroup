import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { exampleSession, sessionAt } from '../src/mocks/data';
import { API, renderApp, server, useScenario } from './helpers';

describe('«Следующая пара» on Today (today.next_lesson)', () => {
  it('shows an upcoming lesson with a relative label', async () => {
    useScenario();
    renderApp();
    expect(await screen.findByText('Следующая пара')).toBeInTheDocument();
    expect(screen.getByText(/^Через 1 ч (15|16) мин$/)).toBeInTheDocument();
    expect(screen.getByText('Ауд. 320 · Иванов А. С.')).toBeInTheDocument();
  });

  it('shows the current lesson with the «Сейчас» badge', async () => {
    useScenario('next-current');
    renderApp();
    expect(await screen.findByText('Идёт сейчас')).toBeInTheDocument();
    expect(screen.getByText('Сейчас')).toBeInTheDocument();
    expect(screen.queryByText('Следующая пара')).not.toBeInTheDocument();
  });

  it('hides the card when next_lesson is null', async () => {
    useScenario('next-null');
    renderApp();
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
    expect(screen.queryByText('Следующая пара')).not.toBeInTheDocument();
    expect(screen.queryByText('Идёт сейчас')).not.toBeInTheDocument();
  });

  it('keeps the lesson when the homework feed is empty', async () => {
    useScenario('empty');
    renderApp();
    expect(await screen.findByText('Следующая пара')).toBeInTheDocument();
    expect(screen.getByText('Математический анализ')).toBeInTheDocument();
    expect(await screen.findByText(/нет заданий|Пока нет|Всё сделано|Заданий нет/i)).toBeInTheDocument();
  });
});

describe('server time, not the phone clock', () => {
  afterEach(() => vi.useRealTimers());

  const SERVER = Date.parse('2026-10-14T09:00:00Z'); // Wednesday

  function serverHandlers() {
    return [
      http.post(`${API}/session/bootstrap`, () => HttpResponse.json(sessionAt(exampleSession, SERVER))),
      http.get(`${API}/today`, async () => {
        const { buildToday, buildWorld } = await import('../src/mocks/data');
        const today = buildToday(buildWorld(SERVER), SERVER);
        return HttpResponse.json({
          ...today,
          next_lesson: {
            state: 'upcoming',
            lesson: {
              id: 'x',
              subject: 'Экономика',
              starts_at: '2026-10-14T09:30:00Z',
              ends_at: '2026-10-14T11:00:00Z',
              teacher: null,
              location: null,
              status: 'scheduled',
            },
          },
        });
      }),
    ];
  }

  it('picks the schedule week from session.server_time even if the phone date is wrong', async () => {
    useScenario();
    let query: URLSearchParams | null = null;
    server.use(
      ...serverHandlers(),
      http.get(`${API}/schedule`, ({ request }) => {
        query = new URL(request.url).searchParams;
        return undefined;
      }),
    );
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2024-02-01T10:00:00Z')); // phone says 2024
    renderApp({ route: '/schedule' });
    await screen.findAllByText(/октября/);
    expect(query).not.toBeNull();
    expect(query!.get('start')).toBe('2026-10-12');
    expect(query!.get('end')).toBe('2026-10-18');
  });

  it('computes «Через …» for the next lesson from server time, not the phone clock', async () => {
    useScenario();
    server.use(...serverHandlers());
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2024-02-01T10:00:00Z'));
    renderApp();
    expect(await screen.findByText('Через 30 мин')).toBeInTheDocument();
  });
});
