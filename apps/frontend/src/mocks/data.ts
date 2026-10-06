/**
 * Development/test fixtures derived from the contract examples (packages/contracts/openapi.yaml).
 * Shapes are checked against the generated types, so no second API model exists here.
 * Never imported by production code paths (see src/mocks/start.ts).
 */
import { contractExamples } from '@studgroup/shared-types/examples';
import { isHomeworkArchived } from '../lib/homeworkArchive';
import { dayKey } from '../lib/format';
import type { NextLesson, LessonOccurrence, ScheduleResponse, HomeworkDetail, HomeworkSummary, SessionBootstrapResponse, SessionContext, TodayResponse } from '@studgroup/shared-types';

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const exampleSession = contractExamples.SessionActive as unknown as SessionBootstrapResponse;
export const exampleNoGroup = contractExamples.SessionNoActiveGroup as unknown as SessionBootstrapResponse;
export const exampleSummary = (contractExamples.TodayPopulated.sections[0]!.items[0]) as unknown as HomeworkSummary;
const exampleDetailAvailable = contractExamples.HomeworkDetailAvailable as unknown as HomeworkDetail;
export const exampleDetailUnavailable = contractExamples.HomeworkDetailUnavailable as unknown as HomeworkDetail;
export const exampleTodayEmpty = contractExamples.TodayEmpty as unknown as TodayResponse;
export const exampleTodayDelayed = contractExamples.TodayDelayed as unknown as TodayResponse;

const iso = (ms: number) => new Date(ms).toISOString();

export interface MockWorld {
  summaries: Map<string, HomeworkSummary>;
  details: Map<string, HomeworkDetail>;
  sections: Array<{ kind: 'overdue' | 'due_today' | 'new_or_changed' | 'upcoming'; ids: string[] }>;
}

/** Builds a world whose dates are relative to `now`, so the UI always looks current. */
export function buildWorld(now: number = Date.now()): MockWorld {
  const summaries = new Map<string, HomeworkSummary>();
  const details = new Map<string, HomeworkDetail>();

  const make = (id: string, summary: HomeworkSummary, detailBase: HomeworkDetail, description: string | null, detailPatch: Partial<HomeworkDetail> = {}) => {
    summaries.set(id, summary);
    details.set(id, { ...detailBase, ...summary, description, ...detailPatch, source_detail: { ...detailBase.source_detail, ...detailPatch.source_detail }, generated_at: iso(now) } as HomeworkDetail);
  };

  const dueTodayId = exampleSummary.id;
  const dueToday: HomeworkSummary = {
    ...exampleSummary,
    verification_state: 'manual_confirmed',
    // Keep this Moscow demo card due today even when previewed late at night.
    deadline: { state: 'known', at: iso(Math.min(now + 8 * HOUR, Date.parse(`${dayKey(now, 'Europe/Moscow')}T23:59:59.999+03:00`))), date_only: false },
    created_at: iso(now - 3 * HOUR),
    updated_at: iso(now - 3 * HOUR),
  };
  make(dueTodayId, dueToday, exampleDetailAvailable, exampleDetailAvailable.description);

  const overdueId = '88888888-8888-4888-8888-888888888888';
  make(
    overdueId,
    {
      ...exampleSummary,
      id: overdueId,
      title: 'Лабораторная работа №3',
      subject: { id: '99999999-9999-4999-8999-999999999999', name: 'Физика' },
      summary: 'Оформить отчёт и защитить',
      urgency: 'super_urgent',
      verification_state: 'manual_confirmed',
      deadline: { state: 'known', at: iso(now - 6 * HOUR), date_only: false },
      created_at: iso(now - 5 * DAY),
      updated_at: iso(now - 2 * DAY),
    },
    exampleDetailAvailable,
    'Оформить отчёт по лабораторной работе №3 и подготовиться к защите.',
  );

  const clarifyId = exampleDetailUnavailable.id;
  make(
    clarifyId,
    {
      ...(exampleDetailUnavailable as unknown as HomeworkSummary),
      created_at: iso(now - 2 * HOUR),
      updated_at: iso(now - 2 * HOUR),
    },
    exampleDetailUnavailable,
    exampleDetailUnavailable.description,
  );

  const reanalyzingId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  make(
    reanalyzingId,
    {
      ...exampleSummary,
      id: reanalyzingId,
      title: 'Эссе по маркетингу',
      subject: { id: '77777777-7777-4777-8777-777777777777', name: 'Маркетинг' },
      summary: 'Две страницы по теме лекции',
      urgency: 'normal',
      verification_state: 'from_group_message',
      deadline: { state: 'known', at: iso(now + 2 * DAY), date_only: true },
      processing: { state: 'reanalyzing', retry_after_seconds: null },
      significant_update: true,
      significant_updated_at: iso(now - HOUR),
      my_state: { completion: 'completed', completed_at: iso(now - 20 * HOUR), completed_revision: 1 },
      created_at: iso(now - 2 * DAY),
      updated_at: iso(now - HOUR),
    },
    exampleDetailAvailable,
    'Написать эссе на две страницы по теме последней лекции.',
  );

  const upcomingId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  make(
    upcomingId,
    {
      ...exampleSummary,
      id: upcomingId,
      title: 'КТ по истории',
      subject: { id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', name: 'История России' },
      summary: 'Контрольная точка по теме «Российская империя»',
      urgency: 'normal',
      verification_state: 'from_group_message',
      deadline: { state: 'known', at: iso(now + 3 * DAY), date_only: false },
      created_at: iso(now - 4 * DAY),
      updated_at: iso(now - 4 * DAY),
    },
    exampleDetailAvailable,
    'Контрольная точка. Повторить материал лекций 5–8.',
  );

  // Items below exist only in the full list («Задания»): they never appear in Today's sections.
  // They cover the cases Today deliberately hides: long-overdue, completed and cancelled.
  const longOverdueId = 'cccccccc-0000-4000-8000-000000000001';
  make(
    longOverdueId,
    {
      ...exampleSummary,
      id: longOverdueId,
      title: 'Реферат по социологии',
      subject: { id: 'dddddddd-0000-4ddd-8ddd-000000000001', name: 'Социология' },
      summary: 'Реферат на 10 страниц',
      urgency: 'normal',
      verification_state: 'manual_confirmed',
      deadline: { state: 'known', at: iso(now - 3 * DAY), date_only: true },
      created_at: iso(now - 9 * DAY),
      updated_at: iso(now - 9 * DAY),
    },
    exampleDetailAvailable,
    'Реферат на 10 страниц по выбранной теме.',
  );

  const doneId = 'cccccccc-0000-4000-8000-000000000002';
  make(
    doneId,
    {
      ...exampleSummary,
      id: doneId,
      title: 'Конспект по философии',
      subject: { id: 'dddddddd-0000-4ddd-8ddd-000000000002', name: 'Философия' },
      summary: 'Конспект первой главы',
      urgency: 'normal',
      verification_state: 'manual_confirmed',
      deadline: { state: 'known', at: iso(now - 2 * DAY), date_only: true },
      my_state: { completion: 'completed', completed_at: iso(now - 3 * DAY), completed_revision: 1 },
      created_at: iso(now - 6 * DAY),
      updated_at: iso(now - 6 * DAY),
    },
    exampleDetailAvailable,
    'Конспект первой главы учебника.',
  );

  const cancelledId = 'cccccccc-0000-4000-8000-000000000003';
  make(
    cancelledId,
    {
      ...exampleSummary,
      id: cancelledId,
      title: 'Доклад по экологии',
      subject: { id: 'dddddddd-0000-4ddd-8ddd-000000000003', name: 'Экология' },
      summary: 'Преподаватель отменил задание',
      urgency: 'normal',
      status: 'cancelled',
      cancelled_at: iso(now - 4 * HOUR),
      verification_state: 'manual_confirmed',
      deadline: { state: 'known', at: iso(now + DAY), date_only: true },
      created_at: iso(now - 3 * DAY),
      updated_at: iso(now - 4 * HOUR),
    },
    exampleDetailAvailable,
    null,
  );

  return {
    summaries,
    details,
    sections: [
      { kind: 'due_today', ids: [dueTodayId] },
      { kind: 'overdue', ids: [overdueId] },
      { kind: 'new_or_changed', ids: [clarifyId, reanalyzingId] },
      { kind: 'upcoming', ids: [upcomingId] },
    ],
  };
}

export function buildToday(world: MockWorld, now: number = Date.now()): TodayResponse {
  const base = contractExamples.TodayPopulated as unknown as TodayResponse;
  const sections = world.sections.map((s) => ({
    kind: s.kind,
    items: s.ids.map((id) => world.summaries.get(id)!).filter((item) => {
      if (item.my_state.completion === 'completed' && item.deadline.state !== 'known') return false;
      return !isHomeworkArchived(item, now, base.group_timezone);
    }),
  })).filter((s) => s.items.length > 0);
  return {
    ...base,
    generated_at: iso(now),
    server_time: iso(now),
    freshness: { last_successful_sync_at: iso(now - 2 * 60_000), stale: false },
    empty: sections.length === 0,
    sections,
  };
}

export function sessionAt(base: SessionBootstrapResponse, now: number = Date.now(), patch?: (s: SessionContext) => SessionContext): SessionBootstrapResponse {
  const withSchedule: SessionContext =
    base.session.permissions.length > 0 && !base.session.permissions.includes('schedule.read')
      ? { ...base.session, permissions: [...base.session.permissions, 'schedule.read'] }
      : base.session;
  const session = { ...withSchedule, server_time: iso(now) };
  return { ...base, expires_at: iso(now + 12 * HOUR), session: patch ? patch(session) : session };
}

const LESSON_TEMPLATE: Array<{ subject: string; start: string; end: string; teacher: string | null; location: string | null }> = [
  { subject: 'История России', start: '08:30', end: '10:00', teacher: 'Смирнова Е. В.', location: 'Ауд. 105' },
  { subject: 'Математический анализ', start: '10:30', end: '12:00', teacher: 'Иванов А. С.', location: 'Ауд. 320' },
  { subject: 'Экономика', start: '12:30', end: '14:00', teacher: null, location: 'Ауд. 212' },
  { subject: 'Английский язык', start: '14:30', end: '16:00', teacher: 'Петрова М. А.', location: null },
];

/**
 * Dev/test-only schedule. The contract (0.2.0) publishes no schedule example, so lessons are
 * generated here in the ScheduleResponse shape (Mon–Fri, Europe/Moscow = +03:00).
 */
export function buildSchedule(start: string, end: string, now: number, variant: 'ready' | 'clarify' | 'empty' = 'ready'): ScheduleResponse {
  const lessons: LessonOccurrence[] = [];
  if (variant !== 'empty') {
    for (let t = Date.parse(`${start}T00:00:00Z`); t <= Date.parse(`${end}T00:00:00Z`); t += DAY) {
      const date = new Date(t);
      const weekday = date.getUTCDay();
      if (weekday === 0 || weekday === 6) continue;
      const key = date.toISOString().slice(0, 10);
      const slice = variant === 'clarify' ? LESSON_TEMPLATE.slice(0, 2) : LESSON_TEMPLATE;
      slice.forEach((l, i) => {
        lessons.push({
          id: `lesson-${key}-${i}`,
          subject: l.subject,
          starts_at: `${key}T${l.start}:00+03:00`,
          ends_at: `${key}T${l.end}:00+03:00`,
          teacher: l.teacher,
          location: l.location,
          status: 'scheduled',
        });
      });
    }
  }
  const clarify = variant === 'clarify';
  return {
    generated_at: iso(now),
    group_timezone: 'Europe/Moscow',
    start,
    end,
    week_state: clarify ? 'needs_clarification' : 'ready',
    first_week_anchor: clarify ? null : start,
    selected_week: clarify ? null : 'first',
    lessons,
  };
}

/** Mimics the backend's server-side selection of `today.next_lesson` (dev/test only). */
export function buildNextLesson(now: number, variant: 'upcoming' | 'current' | 'none'): NextLesson | null {
  if (variant === 'none') return null;
  const start = variant === 'current' ? now - 30 * 60_000 : now + 75 * 60_000;
  return {
    state: variant,
    lesson: {
      id: `next-${variant}`,
      subject: 'Математический анализ',
      starts_at: iso(start),
      ends_at: iso(start + 90 * 60_000),
      teacher: 'Иванов А. С.',
      location: 'Ауд. 320',
      status: 'scheduled',
    },
  };
}

/** Pads the world with generated cards so the list spans several pages (dev/test scenarios only). */
export function addManyHomework(world: MockWorld, now: number, count: number): void {
  const template = [...world.summaries.values()].find((i) => i.title === 'КТ по истории')!;
  const detail = world.details.get(template.id)!;
  for (let n = 1; n <= count; n += 1) {
    const id = `eeeeeeee-0000-4000-8000-${String(n).padStart(12, '0')}`;
    const summary: HomeworkSummary = {
      ...template,
      id,
      title: `Задание ${n}`,
      deadline: { state: 'known', at: iso(now + (n % 20) * HOUR + 2 * DAY), date_only: false },
      created_at: iso(now - 30 * DAY + n * 60_000),
      updated_at: iso(now - 30 * DAY + n * 60_000),
    };
    world.summaries.set(id, summary);
    world.details.set(id, { ...detail, ...summary, description: `Описание задания ${n}` } as HomeworkDetail);
  }
}
