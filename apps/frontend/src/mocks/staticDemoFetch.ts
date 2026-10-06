import type {
  ApiError,
  HomeworkDetail,
  HomeworkListResponse,
  LessonSubjectResponse,
  ScheduleResponse,
  SessionBootstrapResponse,
  SetCompletionRequest,
  TodayResponse,
} from '@studgroup/shared-types';
import { addDays, dayKey } from '../lib/format';
import { buildToday, exampleSession, sessionAt } from './data';
import { GROUP_TZ, isListFilter, selectHomework } from './homeworkList';
import {
  buildReviewedNextLesson,
  buildReviewedSchedule,
  buildReviewedWorld,
  reviewedDeadlineArchived,
  reviewedDeadlines,
} from './reviewedDemoData';

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const error = (code: ApiError['code'], message: string, status: number) =>
  json(
    {
      error: {
        code,
        message,
        correlation_id: 'demo-mobile-preview',
        retryable: status >= 500,
      } satisfies ApiError,
    },
    status,
  );

const demoSession = (now: number): SessionBootstrapResponse =>
  sessionAt(exampleSession, now, (session) => ({
    ...session,
    user: { ...session.user, telegram_user_id: 0, display_name: 'Демо', username: null },
    group: session.group ? { ...session.group, name: 'БИ 1.2 · обезличено' } : null,
  }));

function requestBody(input: RequestInfo | URL, init?: RequestInit): Promise<unknown> {
  if (input instanceof Request) return input.clone().json();
  if (typeof init?.body === 'string') return Promise.resolve(JSON.parse(init.body));
  return Promise.resolve(null);
}

/** Static demo transport. It preserves the generated API client boundary without a Service Worker. */
export function createStaticDemoFetch(): typeof fetch {
  const world = buildReviewedWorld();

  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request?.url ?? String(input));
    const method = (request?.method ?? init?.method ?? 'GET').toUpperCase();
    const path = url.pathname.replace(/\/$/, '');
    const now = Date.now();

    if (method === 'POST' && path.endsWith('/session/bootstrap')) return json(demoSession(now));
    if (method === 'POST' && path.endsWith('/membership/recheck')) return json(demoSession(now).session);

    if (method === 'GET' && path.endsWith('/today')) {
      const nextLesson = buildReviewedNextLesson(now);
      const today = buildToday(world, now);
      if (url.searchParams.get('day') !== 'tomorrow') {
        return json({ ...today, next_lesson: nextLesson } satisfies TodayResponse);
      }
      const selected = addDays(dayKey(now, today.group_timezone), 1);
      const items = today.sections
        .flatMap((section) => section.items)
        .filter(
          (item) =>
            item.deadline.state === 'known' &&
            item.deadline.at &&
            dayKey(item.deadline.at, today.group_timezone) === selected,
        );
      const lesson =
        nextLesson && dayKey(nextLesson.lesson.starts_at, today.group_timezone) === selected
          ? nextLesson
          : null;
      return json({
        ...today,
        sections: items.length ? [{ kind: 'due_today', items }] : [],
        empty: items.length === 0,
        next_lesson: lesson,
      } satisfies TodayResponse);
    }

    if (method === 'GET' && path.endsWith('/deadlines')) {
      const archive = url.searchParams.get('archive') === 'true';
      return json({
        generated_at: new Date(now).toISOString(),
        group_timezone: GROUP_TZ,
        items: reviewedDeadlines.filter((item) => reviewedDeadlineArchived(item, now) === archive),
      });
    }

    const deadlineMatch = path.match(/\/deadlines\/([^/]+)$/);
    if (method === 'GET' && deadlineMatch) {
      const item = reviewedDeadlines.find((candidate) => candidate.id === deadlineMatch[1]);
      return item
        ? json({ generated_at: new Date(now).toISOString(), group_timezone: GROUP_TZ, item })
        : error('not_found', 'Контрольная точка не найдена', 404);
    }

    if (method === 'GET' && path.endsWith('/schedule')) {
      const start = url.searchParams.get('start');
      const end = url.searchParams.get('end');
      if (!start || !end) return error('invalid_request', 'Укажите start и end.', 422);
      return json(buildReviewedSchedule(start, end, now) satisfies ScheduleResponse);
    }

    const lessonMatch = path.match(/\/lessons\/([^/]+)\/subject$/);
    if (method === 'GET' && lessonMatch) {
      const next = buildReviewedNextLesson(now);
      if (!next || next.lesson.id !== lessonMatch[1]) return error('not_found', 'Пара не найдена', 404);
      const homework = buildToday(world, now).sections
        .flatMap((section) => section.items)
        .filter((item) => item.subject.name === next.lesson.subject);
      const events = reviewedDeadlines.filter((item) => item.subject === next.lesson.subject);
      return json({
        generated_at: new Date(now).toISOString(),
        group_timezone: GROUP_TZ,
        lesson: next.lesson,
        homework,
        homework_total: homework.length,
        events_state: 'ready',
        events,
        materials_state: 'not_connected',
      } satisfies LessonSubjectResponse);
    }

    if (method === 'GET' && path.endsWith('/homework')) {
      const filter = url.searchParams.get('filter') ?? 'all';
      if (!isListFilter(filter)) return error('invalid_request', 'Недопустимый фильтр.', 422);
      const stamp = new Date(now).toISOString();
      return json({
        server_time: stamp,
        generated_at: stamp,
        group_timezone: GROUP_TZ,
        freshness: { last_successful_sync_at: stamp, stale: false },
        processing: { state: 'idle', retry_after_seconds: null },
        items: selectHomework([...world.summaries.values()], filter, now),
        next_cursor: null,
      } satisfies HomeworkListResponse);
    }

    const homeworkMatch = path.match(/\/homework\/([^/]+)$/);
    const homeworkId = homeworkMatch?.[1];
    if (homeworkId && method === 'GET') {
      const detail = world.details.get(homeworkId);
      return detail ? json(detail) : error('not_found', 'Задание не найдено.', 404);
    }
    if (homeworkId && method === 'PUT') {
      const detail = world.details.get(homeworkId);
      if (!detail) return error('not_found', 'Задание не найдено.', 404);
      const body = (await requestBody(input, init)) as SetCompletionRequest;
      if (body.expected_revision !== detail.revision) {
        return error('revision_conflict', 'Данные задания изменились.', 409);
      }
      const updated: HomeworkDetail = {
        ...detail,
        my_state: body.completed
          ? { completion: 'completed', completed_at: new Date(now).toISOString(), completed_revision: detail.revision }
          : { completion: 'pending', completed_at: null, completed_revision: null },
      };
      world.details.set(updated.id, updated);
      const summary = world.summaries.get(updated.id);
      if (summary) world.summaries.set(updated.id, { ...summary, my_state: updated.my_state });
      return json(updated);
    }

    return error('not_found', 'Demo endpoint not found.', 404);
  };
}
