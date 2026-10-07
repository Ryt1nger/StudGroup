import type { ApiError, HomeworkListResponse, ScheduleResponse, HomeworkDetail, SessionBootstrapResponse, SetCompletionRequest, TodayResponse } from '@studgroup/shared-types';
import { delay, http, HttpResponse, type HttpHandler } from 'msw';
import { addManyHomework, buildNextLesson, buildSchedule, buildToday, buildWorld, exampleNoGroup, exampleSession, exampleTodayDelayed, exampleTodayEmpty, sessionAt, type MockWorld } from './data';
import type { Scenario } from './scenarios';
import { CURSOR_TTL_MS, decodeCursor, encodeCursor, GROUP_TZ, isListFilter, selectHomework } from './homeworkList';
import { addDays, dayKey } from '../lib/format';
import { buildReviewedNextLesson, buildReviewedSchedule, buildReviewedWorld, reviewedDeadlineArchived, reviewedDeadlines } from './reviewedDemoData';

function apiError(code: ApiError['code'], message: string, status: number, extra: Partial<ApiError> = {}) {
  const error: ApiError = { code, message, correlation_id: '01J9Z4A8Z1R9N2Y3P4Q5W6E7T8', retryable: status >= 500 || status === 429, ...extra };
  return HttpResponse.json({ error }, { status });
}

/** Stateful, contract-shaped API for development and tests. Personal completion persists in memory. */
export function createHandlers(baseUrl: string, scenario: Scenario = 'populated'): HttpHandler[] {
  const publicDemo = import.meta.env.MODE === 'demo';
  const world: MockWorld = publicDemo ? buildReviewedWorld() : buildWorld();
  if (scenario === 'single') {
    world.sections = world.sections.filter((s) => s.kind === 'due_today');
  }
  const now = () => Date.now();
  if (scenario === 'tasks-many' || scenario === 'tasks-cursor-expired' || scenario === 'tasks-more-fails' || scenario === 'tasks-dup-page') addManyHomework(world, now(), 120);
  let cursorExpiredOnce = false;
  let moreFailures = 0;
  let conflictPending = scenario === 'completion-conflict';
  const url = (path: string) => `${baseUrl.replace(/\/$/, '')}${path}`;
  const maybeSlow = async () => {
    if (scenario === 'slow') await delay(2500);
  };

  const handlers: HttpHandler[] = [
    http.get(url('/notifications'), () => HttpResponse.json({ items: [], unread_count: 0, generated_at: new Date().toISOString() })),
    http.post(url('/notifications/read-all'), () => HttpResponse.json({ items: [], unread_count: 0, generated_at: new Date().toISOString() })),
    http.post(url('/session/bootstrap'), async () => {
      await maybeSlow();
      if (scenario === 'offline') return HttpResponse.error();
      if (scenario === 'expired') return apiError('initdata_expired', 'Данные Telegram устарели.', 401);
      if (scenario === 'no-group') return HttpResponse.json(sessionAt(exampleNoGroup, now()));
      if (scenario === 'suspended') {
        return HttpResponse.json(
          sessionAt(exampleSession, now(), (s) => ({
            ...s,
            access_state: 'membership_suspended',
            membership: { ...s.membership!, status: 'suspended', can_recheck: true, next_check_at: null },
            permissions: [],
          })),
        );
      }
      if (scenario === 'left') {
        return HttpResponse.json(sessionAt(exampleSession, now(), (s) => ({ ...s, access_state: 'membership_left', membership: { ...s.membership!, status: 'left', can_recheck: false }, permissions: [] })));
      }
      const active = sessionAt(exampleSession, now(), (session) =>
        publicDemo
          ? {
              ...session,
              user: { ...session.user, telegram_user_id: 0, display_name: 'Демо', username: null },
              group: session.group ? { ...session.group, name: 'БИ 1.2 · обезличено' } : null,
            }
          : session,
      );
      return HttpResponse.json(active satisfies SessionBootstrapResponse);
    }),

    http.post(url('/membership/recheck'), async () => {
      await delay(400);
      return HttpResponse.json(sessionAt(exampleSession, now()).session);
    }),

    http.get(url('/today'), async ({ request }) => {
      await maybeSlow();
      if (scenario === 'offline') return HttpResponse.error();
      if (scenario === 'forbidden') return apiError('permission_denied', 'Нет доступа.', 403);
      if (scenario === 'service-error') return apiError('service_unavailable', 'Сервис недоступен.', 503);
      const stamp = { server_time: new Date(now()).toISOString(), generated_at: new Date(now()).toISOString() };
      // `next_lesson` is independent of `empty`: a feed without homework still shows the lesson.
      const next_lesson = publicDemo
        ? buildReviewedNextLesson(now())
        : buildNextLesson(now(), scenario === 'next-current' ? 'current' : scenario === 'next-null' ? 'none' : 'upcoming');
      let today: TodayResponse;
      if (scenario === 'empty') today = { ...exampleTodayEmpty, ...stamp };
      else if (scenario === 'delayed') today = { ...exampleTodayDelayed, ...stamp };
      else if (scenario === 'updating') today = { ...exampleTodayEmpty, processing: { state: 'updating', retry_after_seconds: 10 }, ...stamp };
      else {
        today = buildToday(world, now());
        if (scenario === 'stale') today.freshness = { last_successful_sync_at: new Date(now() - 40 * 60_000).toISOString(), stale: true };
      }
      if (new URL(request.url).searchParams.get('day') === 'tomorrow') {
        const selected = addDays(dayKey(stamp.server_time, today.group_timezone), 1);
        const items = today.sections.flatMap((section) => section.items).filter((item) => item.deadline.state === 'known' && item.deadline.at && dayKey(item.deadline.at, today.group_timezone) === selected);
        const lesson = next_lesson && dayKey(next_lesson.lesson.starts_at, today.group_timezone) === selected ? next_lesson : null;
        return HttpResponse.json({ ...today, sections: items.length ? [{ kind: 'due_today', items }] : [], empty: items.length === 0, next_lesson: lesson } satisfies TodayResponse);
      }
      return HttpResponse.json({ ...today, next_lesson } satisfies TodayResponse);
    }),

    http.get(url('/deadlines'), ({ request }) => {
      const archive = new URL(request.url).searchParams.get('archive') === 'true';
      const items = publicDemo ? reviewedDeadlines.filter((item) => reviewedDeadlineArchived(item, now()) === archive) : [];
      return HttpResponse.json({ generated_at: new Date(now()).toISOString(), group_timezone: GROUP_TZ, items });
    }),

    http.get(url('/deadlines/:deadline_id'), ({ params }) => {
      const item = publicDemo ? reviewedDeadlines.find((candidate) => candidate.id === params.deadline_id) : null;
      if (!item) return apiError('not_found', 'Контрольная точка не найдена', 404);
      return HttpResponse.json({ generated_at: new Date(now()).toISOString(), group_timezone: GROUP_TZ, item });
    }),

    http.get(url('/lessons/:lesson_id/subject'), ({ params }) => {
      const next = publicDemo ? buildReviewedNextLesson(now()) : buildNextLesson(now(), scenario === 'next-current' ? 'current' : 'upcoming');
      if (!next || next.lesson.id !== params.lesson_id) return apiError('not_found', 'Пара не найдена', 404);
      const homework = buildToday(world, now()).sections.flatMap((section) => section.items).filter((item) => item.subject.name === next.lesson.subject);
      return HttpResponse.json({ generated_at: new Date(now()).toISOString(), group_timezone: GROUP_TZ, lesson: next.lesson, homework, homework_total: homework.length, events_state: 'not_connected', materials_state: 'not_connected' });
    }),

    http.get(url('/schedule'), async ({ request }) => {
      await maybeSlow();
      if (scenario === 'offline') return HttpResponse.error();
      if (scenario === 'schedule-error') return apiError('service_unavailable', 'Сервис недоступен.', 503);
      if (scenario === 'schedule-forbidden') return apiError('permission_denied', 'Нет доступа.', 403);
      const params = new URL(request.url).searchParams;
      const start = params.get('start');
      const end = params.get('end');
      if (!start || !end) return apiError('invalid_request', 'Укажите start и end.', 422);
      const variant = scenario === 'schedule-clarify' ? 'clarify' : scenario === 'schedule-empty' ? 'empty' : 'ready';
      return HttpResponse.json((publicDemo ? buildReviewedSchedule(start, end, now()) : buildSchedule(start, end, now(), variant)) satisfies ScheduleResponse);
    }),

    // Canonical GET /homework (contract 0.3.0): server-side filters, created_at/id ordering, opaque
    // 15-minute cursors bound to the filter that freeze the selection time.
    http.get(url('/homework'), async ({ request }) => {
      await maybeSlow();
      if (scenario === 'offline') return HttpResponse.error();
      if (scenario === 'forbidden') return apiError('permission_denied', 'Нет доступа.', 403);
      if (scenario === 'tasks-error') return apiError('service_unavailable', 'Сервис недоступен.', 503);
      const params = new URL(request.url).searchParams;
      const filter = params.get('filter') ?? 'all';
      const limit = Number(params.get('limit') ?? 50);
      const cursorToken = params.get('cursor');
      if (!isListFilter(filter) || !Number.isInteger(limit) || limit < 1 || limit > 100) {
        return apiError('invalid_request', 'Недопустимые параметры списка.', 422);
      }
      let offset = 0;
      let selectedAt = now();
      if (cursorToken) {
        if (scenario === 'tasks-cursor-expired' && !cursorExpiredOnce) {
          cursorExpiredOnce = true;
          return apiError('invalid_request', 'Курсор устарел. Начните список заново.', 422);
        }
        // Fails until the client's automatic retries (2) are used up, so the user sees the failure.
        if (scenario === 'tasks-more-fails' && moreFailures < 3) {
          moreFailures += 1;
          return apiError('service_unavailable', 'Сервис недоступен.', 503);
        }
        const cursor = decodeCursor(cursorToken);
        if (!cursor || cursor.f !== filter || now() > cursor.t + CURSOR_TTL_MS) {
          return apiError('invalid_request', 'Курсор недействителен или устарел.', 422);
        }
        offset = cursor.o;
        selectedAt = cursor.t;
      }
      const stamp = new Date(now()).toISOString();
      const base = { server_time: new Date(selectedAt).toISOString(), generated_at: stamp, group_timezone: GROUP_TZ, freshness: { last_successful_sync_at: stamp, stale: false }, processing: { state: 'idle' as const, retry_after_seconds: null } };
      if (scenario === 'tasks-empty' || scenario === 'empty') {
        return HttpResponse.json({ ...base, items: [], next_cursor: null } satisfies HomeworkListResponse);
      }
      if (scenario === 'tasks-empty-first-page' && !cursorToken) {
        // An empty page does not mean an empty list while a cursor is returned.
        return HttpResponse.json({ ...base, items: [], next_cursor: encodeCursor({ f: filter, o: 0, t: selectedAt }) } satisfies HomeworkListResponse);
      }
      const selected = selectHomework([...world.summaries.values()], filter, selectedAt);
      let items = selected.slice(offset, offset + limit);
      if (scenario === 'tasks-dup-page' && offset > 0 && selected[0]) items = [selected[0], ...items];
      const next = offset + limit < selected.length ? encodeCursor({ f: filter, o: offset + limit, t: selectedAt }) : null;
      return HttpResponse.json({ ...base, items, next_cursor: next } satisfies HomeworkListResponse);
    }),

    http.get(url('/homework/:id'), async ({ params }) => {
      await maybeSlow();
      if (scenario === 'offline') return HttpResponse.error();
      const id = String(params.id);
      if (id === '00000000-0000-4000-8000-000000000410') return apiError('gone', 'Эти данные больше не хранятся.', 410);
      const detail = world.details.get(id);
      if (!detail) return apiError('not_found', 'Задание не найдено.', 404);
      return HttpResponse.json(detail);
    }),

    http.put(url('/homework/:id/completion'), async ({ params, request }) => {
      await delay(350);
      const id = String(params.id);
      const stored = world.details.get(id);
      if (!stored) return apiError('not_found', 'Задание не найдено.', 404);
      const body = (await request.json()) as SetCompletionRequest;
      if (scenario === 'completion-fails') return apiError('service_unavailable', 'Сервис недоступен.', 503);
      if (conflictPending) {
        // One-shot conflict: another editor changed the card, so its revision moves on.
        conflictPending = false;
        world.details.set(id, { ...stored, revision: stored.revision + 1 });
      }
      const detail = world.details.get(id) ?? stored;
      if (body.expected_revision !== detail.revision) {
        return apiError('revision_conflict', 'Данные задания изменились. Обновите карточку и повторите действие.', 409, {
          retryable: true,
          details: { resource_id: id, expected_revision: body.expected_revision, current_revision: detail.revision, action: 'refetch' },
        });
      }
      const updated: HomeworkDetail = {
        ...detail,
        my_state: body.completed
          ? { completion: 'completed', completed_at: new Date(now()).toISOString(), completed_revision: detail.revision }
          : { completion: 'pending', completed_at: null, completed_revision: null },
      };
      world.details.set(id, updated);
      const summary = world.summaries.get(id);
      if (summary) world.summaries.set(id, { ...summary, my_state: updated.my_state });
      // Completion changes the personal mark, not section membership. buildToday applies visibility.
      return HttpResponse.json(updated);
    }),
  ];
  return handlers;
}
