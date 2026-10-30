import type { ConfirmImageScheduleSuggestionData, ScheduleSuggestion } from '@studgroup/shared-types';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { expect, it } from 'vitest';
import { buildSchedule, exampleSession, sessionAt } from '../src/mocks/data';
import { API, renderApp, server, useScenario as loadScenario } from './helpers';

const ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const SUBGROUP = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const SUBJECT = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';

function prepareReview({ stale = false, missingDates = false } = {}) {
  loadScenario();
  let version = stale ? 1 : 0;
  let state: ScheduleSuggestion['state'] = 'pending';
  const requests: ConfirmImageScheduleSuggestionData['body'][] = [];
  const rows: ScheduleSuggestion['rows'] = [
    {
      weekday: 0,
      subject: 'Математический анализ',
      starts: '10:00',
      ends: '11:30',
      week: 'all',
      valid_from: missingDates ? null : '2026-10-05',
      valid_until: missingDates ? null : '2026-12-31',
      subgroup: 'Первая',
      room: '201',
      teacher: 'Иванов',
    },
  ];
  const schedule = buildSchedule('2026-10-05', '2026-10-11', Date.now());
  const lesson = schedule.lessons[0]!;
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(
        sessionAt(exampleSession, Date.now(), (session) => ({
          ...session,
          membership: session.membership ? { ...session.membership, role: 'headman' } : null,
          features: { schedule_review: true },
        })),
      ),
    ),
    http.get(`${API}/subjects`, () =>
      HttpResponse.json({
        generated_at: new Date().toISOString(),
        group_timezone: 'Europe/Moscow',
        items: [{ id: SUBJECT, name: 'Математический анализ', homework_count: 0, deadline_count: 0 }],
      }),
    ),
    http.get(`${API}/staff/image-findings/schedule`, () =>
      HttpResponse.json({
        suggestions: [
          {
            id: ID,
            revision: 1,
            state,
            schedule_version: 0,
            rows,
            confirmation: null,
            source_url: 'https://t.me/c/123456/31',
          },
        ],
        current_schedule_version: version,
        subgroups: [{ id: SUBGROUP, subject_id: SUBJECT, subject: 'Математический анализ', name: 'Первая' }],
      }),
    ),
    http.get(`${API}/staff/schedule`, () =>
      HttpResponse.json({
        schedule,
        schedule_version: version,
        no_lesson_days: [],
        lesson_edits: [
          { lesson_id: lesson.id, pattern_revision: 7, override_revision: 3, week: 'all', single_day: false },
        ],
      }),
    ),
    http.post(`${API}/staff/image-findings/schedule/${ID}/confirm`, async ({ request }) => {
      requests.push((await request.json()) as ConfirmImageScheduleSuggestionData['body']);
      version += 1;
      state = 'confirmed';
      return HttpResponse.json({ schedule_version: version });
    }),
  );
  return { requests, rows, lesson };
}

it('corrects rows, maps a subgroup and an existing occurrence, then confirms once', async () => {
  const { requests, rows, lesson } = prepareReview();
  const original = JSON.stringify(rows);
  const app = renderApp({ route: '/schedule/review' });
  const teacher = await screen.findByLabelText('Преподаватель');
  await userEvent.clear(teacher);
  await userEvent.type(teacher, 'Петров');
  await userEvent.selectOptions(screen.getByLabelText('Подгруппа'), SUBGROUP);
  await waitFor(() => expect(screen.getByLabelText('Заменить существующую пару')).toContainHTML(lesson.id));
  await userEvent.selectOptions(screen.getByLabelText('Заменить существующую пару'), lesson.id);
  await userEvent.selectOptions(screen.getByLabelText('Область изменения'), 'recurring');
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  await waitFor(() => expect(app.router.state.location.pathname).toBe('/schedule'));
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    expected_revision: 1,
    expected_schedule_version: 0,
    corrected_rows: [
      {
        subject_id: SUBJECT,
        subgroup_id: SUBGROUP,
        teacher: 'Петров',
        valid_from: '2026-10-05',
        valid_until: '2026-12-31',
      },
    ],
    targets: [
      { row_index: 0, lesson_id: lesson.id, pattern_revision: 7, override_revision: 3, scope: 'recurring' },
    ],
  });
  expect(JSON.stringify(rows)).toBe(original);
});

it('does not invent effective dates and requires the editor to map a recognized subgroup', async () => {
  const { requests } = prepareReview({ missingDates: true });
  renderApp({ route: '/schedule/review' });
  expect(await screen.findByLabelText('Действует с')).toHaveValue('');
  expect(screen.getByLabelText('Действует до')).toHaveValue('');
  await userEvent.type(screen.getByLabelText('Действует с'), '2026-10-05');
  await userEvent.type(screen.getByLabelText('Действует до'), '2026-12-31');
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  expect(await screen.findByText(/Сопоставьте распознанную подгруппу/)).toBeInTheDocument();
  expect(requests).toHaveLength(0);
  await userEvent.selectOptions(screen.getByLabelText('Подгруппа'), '');
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]?.corrected_rows?.[0]?.subgroup_id).toBeNull();
});

it('requires explicit re-review when the official schedule changed since recognition', async () => {
  const { requests } = prepareReview({ stale: true });
  renderApp({ route: '/schedule/review' });
  await userEvent.selectOptions(await screen.findByLabelText('Подгруппа'), SUBGROUP);
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  expect(
    await screen.findByText('Подтвердите повторную проверку действующего расписания'),
  ).toBeInTheDocument();
  expect(requests).toHaveLength(0);
  await userEvent.click(screen.getByRole('checkbox', { name: /Действующее расписание изменилось/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({ expected_schedule_version: 1, acknowledge_schedule_change: true });
});

it('does not query editor data without the group capability', async () => {
  loadScenario();
  renderApp({ route: '/schedule/review' });
  expect(
    await screen.findByText('Подтверждать расписание может староста или заместитель.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Подтвердить расписание' })).not.toBeInTheDocument();
});
