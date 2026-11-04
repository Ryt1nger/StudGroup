import { client, type ConfirmImageScheduleSuggestionData, type ScheduleSuggestion } from '@studgroup/shared-types';
import { cabinetScheduleUrl } from '../src/features/schedule/cabinetScheduleApi';
import { fireEvent, screen, waitFor } from '@testing-library/react';
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
  await userEvent.click(await screen.findByRole('button', { name: /Редактировать пару: Математический анализ/ }));
  const teacher = await screen.findByLabelText('Преподаватель');
  await userEvent.clear(teacher);
  await userEvent.type(teacher, 'Петров');
  await userEvent.selectOptions(screen.getByLabelText('Подгруппа'), SUBGROUP);
  await waitFor(() => expect(screen.getByLabelText('Заменить существующую пару')).toContainHTML(lesson.id));
  await userEvent.selectOptions(screen.getByLabelText('Заменить существующую пару'), lesson.id);
  await userEvent.selectOptions(screen.getByLabelText('Область изменения'), 'recurring');
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  await waitFor(() => expect(app.router.state.location.pathname).toBe('/schedule/manage'));
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
  await userEvent.click(await screen.findByRole('button', { name: /Редактировать пару: Математический анализ/ }));
  expect(await screen.findByLabelText('Действует с')).toHaveValue('');
  expect(screen.getByLabelText('Действует до')).toHaveValue('');
  await userEvent.type(screen.getByLabelText('Действует с'), '2026-10-05');
  await userEvent.type(screen.getByLabelText('Действует до'), '2026-12-31');
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
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
  await userEvent.click(await screen.findByRole('button', { name: /Редактировать пару: Математический анализ/ }));
  await userEvent.selectOptions(await screen.findByLabelText('Подгруппа'), SUBGROUP);
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  expect(
    await screen.findByText('Подтвердите повторную проверку действующего расписания'),
  ).toBeInTheDocument();
  expect(requests).toHaveLength(0);
  await userEvent.click(screen.getByRole('checkbox', { name: /Действующее расписание изменилось/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить расписание' }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({ expected_schedule_version: 1, acknowledge_schedule_change: true });
});

it('reviews lessons as day cards and opens the separate edit view', async () => {
  prepareReview();
  renderApp({ route: '/schedule/review' });
  const card = await screen.findByRole('button', { name: 'Редактировать пару: Математический анализ' });
  expect(card).toHaveTextContent('10:00');
  expect(screen.queryByLabelText('Преподаватель')).not.toBeInTheDocument();
  await userEvent.click(card);
  expect(await screen.findByLabelText('Преподаватель')).toHaveValue('Иванов');
  await userEvent.click(screen.getByRole('button', { name: 'Готово' }));
  expect(await screen.findByRole('button', { name: 'Редактировать пару: Математический анализ' })).toBeInTheDocument();
});

it('does not query editor data without the group capability', async () => {
  loadScenario();
  renderApp({ route: '/schedule/review' });
  expect(
    await screen.findByText('Подтверждать расписание может староста или заместитель.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Подтвердить расписание' })).not.toBeInTheDocument();
});


it('uses the configured API URL once for private screenshot uploads', () => {
  const original = client.getConfig().baseUrl;
  try {
    client.setConfig({ baseUrl: 'https://api.studgroup.example/v1' });
    expect(cabinetScheduleUrl('/v1/staff/schedule-upload'))
      .toBe('https://api.studgroup.example/v1/staff/schedule-upload');
    client.setConfig({ baseUrl: 'https://api.studgroup.example/v1/' });
    expect(cabinetScheduleUrl('/v1/staff/schedule-upload/123/image'))
      .toBe('https://api.studgroup.example/v1/staff/schedule-upload/123/image');
  } finally {
    client.setConfig({ baseUrl: original });
  }
});

it('shows private screenshot review without Telegram image-review capability', async () => {
  loadScenario();
  const schedule = buildSchedule('2026-10-05', '2026-10-11', Date.now());
  const lesson = schedule.lessons[0]!;
  let officialReads = 0;
  let telegramReads = 0;
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(sessionAt(exampleSession, Date.now(), (session) => ({
        ...session,
        features: { schedule_review: false },
        membership: session.membership ? { ...session.membership, role: 'headman' } : null,
        permissions: [...new Set([...session.permissions, 'schedule.write' as const])],
      }))),
    ),
    http.get(`${API}/staff/schedule-upload`, () => HttpResponse.json({
      current_schedule_version: 0,
      subgroups: [],
      suggestions: [{
        id: ID,
        state: 'pending',
        revision: 1,
        schedule_version: 0,
        week_start: '2026-10-05',
        duration: 'one_week',
        source_url: null,
        confirmation: null,
        rows: [{
          weekday: 0,
          starts: '09:00',
          ends: '10:20',
          subject: 'Математический анализ',
          teacher: 'Иванов',
          room: '305',
          week: 'all',
          valid_from: '2026-10-05',
          valid_until: '2026-10-11',
        }],
      }],
    })),
    http.get(`${API}/staff/schedule-upload/${ID}/image`, () =>
      new HttpResponse(new Uint8Array([137, 80, 78, 71]), {
        headers: { 'Content-Type': 'image/png' },
      })),
    http.get(`${API}/staff/schedule`, () => {
      officialReads += 1;
      return HttpResponse.json({
        schedule, schedule_version: 0, no_lesson_days: [],
        lesson_edits: [{
          lesson_id: lesson.id,
          pattern_revision: 1,
          override_revision: 0,
          week: 'all',
          single_day: false,
        }],
      });
    }),
    http.get(`${API}/staff/image-findings/schedule`, () => {
      telegramReads += 1;
      return HttpResponse.json({ suggestions: [] });
    }),
  );
  renderApp({ route: '/schedule/review' });
  const mapping = await screen.findByRole('combobox', { name: 'Заменить существующую пару' });
  await waitFor(() => expect(mapping).toContainHTML(lesson.id));
  expect(officialReads).toBeGreaterThan(0);
  expect(telegramReads).toBe(0);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Подтвердить расписание' }))
    .toBeEnabled());
});

it('shows an empty explanation for staff with no drafts when Telegram review is off', async () => {
  loadScenario();
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(sessionAt(exampleSession, Date.now(), (session) => ({
        ...session,
        features: { schedule_review: false },
        membership: session.membership ? { ...session.membership, role: 'deputy' } : null,
      }))),
    ),
    http.get(`${API}/staff/schedule-upload`, () =>
      HttpResponse.json({ current_schedule_version: 0, subgroups: [], suggestions: [] })),
  );
  renderApp({ route: '/schedule/review' });
  expect(await screen.findByText('Пока нет черновиков')).toBeInTheDocument();
});


it('keeps upload messaging simple when AI funding is unavailable', async () => {
  loadScenario();
  let attempts = 0;
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(sessionAt(exampleSession, Date.now(), (session) => ({
        ...session,
        features: { schedule_review: false },
        membership: session.membership ? { ...session.membership, role: 'headman' } : null,
        permissions: [...new Set([...session.permissions, 'schedule.write' as const])],
      }))),
    ),
    http.get(`${API}/staff/schedule-upload`, () =>
      HttpResponse.json({ current_schedule_version: 0, subgroups: [], suggestions: [] })),
    http.post(`${API}/staff/schedule-upload`, () => {
      attempts += 1;
      return HttpResponse.json({
        error: {
          code: 'analysis_budget_closed',
          message: 'Платный анализ не настроен. Требуется ранее подтверждённый лимит расходов.',
        },
      }, { status: 409 });
    }),
  );
  renderApp({ route: '/schedule/review' });
  const form = await screen.findByRole('form', { name: 'Загрузка скриншота расписания' });
  expect(form).toHaveTextContent('Загрузить расписание');
  expect(form).not.toHaveTextContent('два платных запроса');
  await userEvent.upload(
    screen.getByLabelText(/Скриншот расписания/),
    new File(['test'], 'schedule.png', { type: 'image/png' }),
  );
  // JSDOM's native file-form constraint handling can block userEvent.click.
  // Submit the form to test the actual mutation and its error presentation.
  fireEvent.submit(form);
  await waitFor(() => expect(attempts).toBe(1));
  expect(await screen.findByRole('alert')).toHaveTextContent('Распознавание сейчас недоступно');
  expect(form).not.toHaveTextContent('Платный анализ не настроен');
  expect(screen.getByRole('button', { name: 'Анализировать' })).toBeEnabled();
});
