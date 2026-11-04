import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { expect, it } from 'vitest';
import { exampleSession, sessionAt } from '../src/mocks/data';
import { API, renderApp, server, useScenario } from './helpers';

const ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

function staff() {
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(sessionAt(exampleSession, Date.now(), (session) => ({
        ...session,
        features: { schedule_review: false },
        membership: session.membership ? { ...session.membership, role: 'headman' } : null,
        permissions: [...new Set([...session.permissions, 'schedule.write' as const])],
      }))),
    ),
  );
}

it('submits a screenshot without waiting and shows server-confirmed progress across refetches', async () => {
  useScenario();
  staff();
  let stage: 'queued' | 'processing' | 'pending' = 'queued';
  let percent = 5;
  let phase = 'queued_a';
  let posts = 0;
  const draft = () => ({
    id: ID,
    state: stage,
    progress_percent: percent,
    progress_stage: phase,
    can_retry: false,
    revision: 1,
    schedule_version: 0,
    week_start: '2026-11-02',
    duration: 'one_week',
    source_url: null,
    confirmation: null,
    rows: stage === 'pending' ? [{
      weekday: 0, subject: 'Математика', starts: '09:00', ends: '10:20', week: 'all',
      valid_from: '2026-11-02', valid_until: '2026-11-08',
    }] : [],
  });
  server.use(
    http.get(`${API}/staff/schedule-upload`, () => HttpResponse.json({
      current_schedule_version: 0,
      subgroups: [],
      suggestions: posts ? [draft()] : [],
    })),
    http.post(`${API}/staff/schedule-upload`, () => {
      posts += 1;
      return HttpResponse.json(draft(), { status: 201 });
    }),
    http.get(`${API}/staff/schedule-upload/${ID}/image`, () =>
      new HttpResponse(new Uint8Array([137, 80, 78, 71]), {
        headers: { 'Content-Type': 'image/png' },
      })),
  );
  const app = renderApp({ route: '/schedule/review' });
  const form = await screen.findByRole('form', { name: 'Загрузка скриншота расписания' });
  await userEvent.upload(
    screen.getByLabelText(/Скриншот расписания/),
    new File(['fake pixels'], 'lesson.png', { type: 'image/png' }),
  );
  fireEvent.submit(form);
  await waitFor(() => expect(posts).toBe(1));
  const progress = await screen.findByRole('progressbar', { name: 'Готовность распознавания' });
  expect(progress).toHaveAttribute('aria-valuenow', '5');
  expect(screen.getByText(/Можно закрыть приложение/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Подтвердить расписание' })).not.toBeInTheDocument();

  stage = 'processing';
  percent = 65;
  phase = 'reading_b';
  await act(async () => { await app.queryClient.invalidateQueries({ queryKey: ['cabinet-schedule-review'] }); });
  await waitFor(() => expect(progress).toHaveAttribute('aria-valuenow', '65'));
  expect(screen.getByText('Проверяем пары и даты')).toBeInTheDocument();

  stage = 'pending';
  percent = 100;
  phase = 'ready';
  await act(async () => { await app.queryClient.invalidateQueries({ queryKey: ['cabinet-schedule-review'] }); });
  await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument());
  expect(await screen.findByRole('button', { name: /Редактировать пару:/ })).toBeInTheDocument();
});

it('shows a failed persisted task and allows only an explicit retry', async () => {
  useScenario();
  staff();
  let retried = false;
  server.use(
    http.get(`${API}/staff/schedule-upload`, () => HttpResponse.json({
      current_schedule_version: 0,
      subgroups: [],
      suggestions: [{
        id: ID, state: retried ? 'queued' : 'failed', progress_percent: 20,
        progress_stage: retried ? 'queued_a' : 'failed', can_retry: !retried,
        revision: 1, schedule_version: 0, week_start: '2026-11-02',
        duration: 'one_week', source_url: null, confirmation: null, rows: [],
      }],
    })),
    http.post(`${API}/staff/schedule-upload/${ID}/retry`, () => {
      retried = true;
      return HttpResponse.json({ state: 'queued' }, { status: 202 });
    }),
  );
  renderApp({ route: '/schedule/review' });
  expect(await screen.findByText('Обработка остановлена')).toBeInTheDocument();
  expect(retried).toBe(false);
  await userEvent.click(screen.getByRole('button', { name: 'Перезапустить анализ' }));
  await waitFor(() => expect(retried).toBe(true));
  expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '20');
});
