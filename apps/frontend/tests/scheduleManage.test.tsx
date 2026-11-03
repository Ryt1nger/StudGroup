import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { buildSchedule, exampleSession, sessionAt } from '../src/mocks/data';
import { weekRange } from '../src/lib/format';
import { API, renderApp, server, useScenario } from './helpers';

function allowHeadman() {
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(sessionAt(exampleSession, Date.now(), (session) => ({
        ...session,
        membership: session.membership ? { ...session.membership, role: 'headman' } : null,
        permissions: [...new Set([...session.permissions, 'schedule.write' as const])],
      }))),
    ),
  );
}

describe('Headman schedule management', () => {
  it('keeps the large upload button off the timetable and uses a discreet staff pencil', async () => {
    useScenario();
    allowHeadman();
    const app = renderApp({ route: '/schedule' });
    const pencil = await screen.findByRole('link', { name: 'Управление расписанием' });
    expect(pencil).toHaveAttribute('href', '/schedule/manage');
    expect(screen.queryByText('Добавить расписание по скриншоту')).not.toBeInTheDocument();
    await userEvent.click(pencil);
    await waitFor(() => expect(app.router.state.location.pathname).toBe('/schedule/manage'));
    expect(await screen.findByRole('heading', { name: 'Управление расписанием' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Загрузить расписание и открыть черновики' }))
      .toHaveAttribute('href', '/schedule/review');
  });

  it('does not show the pencil or request the private timetable for students', async () => {
    useScenario();
    const student = renderApp({ route: '/schedule' });
    expect((await screen.findAllByText('Расписание')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('link', { name: 'Управление расписанием' })).not.toBeInTheDocument();
    student.unmount();

    let count = 0;
    server.use(http.get(`${API}/staff/schedule`, () => {
      count += 1;
      return HttpResponse.json({}, { status: 403 });
    }));
    renderApp({ route: '/schedule/manage' });
    expect(await screen.findByText(/Изменять расписание могут староста и заместитель/)).toBeInTheDocument();
    expect(count).toBe(0);
  });

  it('reviews existing lessons and applies changes only when Save is pressed', async () => {
    useScenario();
    allowHeadman();
    const range = weekRange(Date.now(), 'Europe/Moscow');
    const schedule = buildSchedule(range.start, range.end, Date.now());
    const lesson = schedule.lessons[0]!;
    const changed: unknown[] = [];
    server.use(
      http.get(`${API}/staff/schedule`, () =>
        HttpResponse.json({
          schedule,
          lesson_edits: schedule.lessons.map((row) => ({
            lesson_id: row.id, pattern_revision: 1, override_revision: 0,
            week: 'all', single_day: false,
          })),
          no_lesson_days: [],
          schedule_version: 0,
        }),
      ),
      http.patch(`${API}/staff/schedule/lessons/:lesson_id`, async ({ request }) => {
        changed.push(await request.json());
        return HttpResponse.json({ schedule_version: 1 });
      }),
    );
    renderApp({ route: '/schedule/manage' });
    const edit = (await screen.findAllByRole('button', { name: `Изменить пару: ${lesson.subject}` }))[0]!;
    expect(changed).toHaveLength(0);
    await userEvent.click(edit);
    const form = screen.getByRole('form', { name: `Редактирование: ${lesson.subject}` });
    const subject = within(form).getByLabelText('Предмет');
    await userEvent.clear(subject);
    await userEvent.type(subject, 'Физика');
    expect(changed).toHaveLength(0);
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(changed).toHaveLength(1));
    expect(changed[0]).toMatchObject({
      scope: 'once',
      pattern_revision: 1,
      override_revision: 0,
      subject: 'Физика',
    });
  });
});
