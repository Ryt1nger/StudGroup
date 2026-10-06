/**
 * Public-demo projection of the reviewed 4 Sep–5 Oct 2026 group analysis.
 *
 * Deliberately excluded: raw chat text, Telegram/user identifiers, participant names,
 * source message ids and preview secrets. Only reviewed academic facts are represented.
 */
import type {
  AcademicDeadline,
  HomeworkDetail,
  HomeworkSummary,
  LessonOccurrence,
  NextLesson,
  ScheduleResponse,
} from '@studgroup/shared-types';
import { dayKey } from '../lib/format';
import { exampleDetailUnavailable, exampleSummary, type MockWorld } from './data';

const DAY = 86_400_000;
const GROUP_TIMEZONE = 'Europe/Moscow';

type ReviewedHomework = {
  subject: string;
  title: string;
  deadline: string;
  dateOnly: boolean;
  verification: HomeworkSummary['verification_state'];
  createdAt: string;
  updatedAt: string;
};

const reviewedHomework: ReviewedHomework[] = [
  { subject: 'Русский язык как…', title: 'Написать пять пар терминов-паронимов', deadline: '2026-10-10T11:00:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-09-08T18:15:23Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'Математический анализ', title: 'Стр. 37: вопросы 1–9', deadline: '2026-10-07T07:40:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-09-10T09:38:26Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'История России', title: 'Повторить середину XII — конец XIII века и управление Новгорода', deadline: '2026-10-06T09:20:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-09-15T12:55:21Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'Математический анализ', title: 'Теория функций: вопросы 1–10 и 14–18, таблица на стр. 28', deadline: '2026-09-16T21:00:00Z', dateOnly: true, verification: 'needs_clarification', createdAt: '2026-09-16T08:22:07Z', updatedAt: '2026-10-06T07:11:34Z' },
  { subject: 'Математический анализ', title: 'Решить №12, 13, 14 на стр. 43', deadline: '2026-10-06T21:00:00Z', dateOnly: true, verification: 'inferred', createdAt: '2026-09-16T08:23:06Z', updatedAt: '2026-10-06T07:16:19Z' },
  { subject: 'Линейная алгебра и…', title: 'Научиться вычислять обратную матрицу', deadline: '2026-10-07T09:20:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-09-23T10:27:06Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'Математический анализ', title: 'Вопросы 1–25, свойства функций и №15, 16.2, 16.3', deadline: '2026-10-07T07:40:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-09-24T09:36:51Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'Основы российской государственности', title: 'Групповые доклады: подходы и теории', deadline: '2026-10-12T09:20:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-09-28T11:12:52Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'История России', title: 'Практическая работа № 3. Русские земли в середине XIII–XIV вв.', deadline: '2026-09-29T11:02:27Z', dateOnly: false, verification: 'needs_clarification', createdAt: '2026-10-05T17:32:52Z', updatedAt: '2026-10-06T07:11:34Z' },
  { subject: 'Культура личности и общества', title: 'Тест по культуре', deadline: '2026-10-03T21:00:00Z', dateOnly: true, verification: 'needs_clarification', createdAt: '2026-10-05T17:32:52Z', updatedAt: '2026-10-06T07:11:34Z' },
  { subject: 'Математический анализ', title: '16.1 б стр. 44', deadline: '2026-10-06T21:00:00Z', dateOnly: true, verification: 'inferred', createdAt: '2026-10-05T17:32:52Z', updatedAt: '2026-10-06T07:16:19Z' },
  { subject: 'Математический анализ', title: 'Стр. 46 № 10.1', deadline: '2026-10-07T07:40:00Z', dateOnly: false, verification: 'inferred', createdAt: '2026-10-05T17:32:52Z', updatedAt: '2026-10-06T08:44:44Z' },
  { subject: 'Математический анализ', title: 'Номера с 10 по 16 (стр. 43–44)', deadline: '2026-10-06T21:00:00Z', dateOnly: true, verification: 'needs_clarification', createdAt: '2026-10-05T17:32:52Z', updatedAt: '2026-10-06T02:23:33Z' },
];

const subjectIds = new Map<string, string>();
const subjectId = (name: string) => {
  if (!subjectIds.has(name)) subjectIds.set(name, `20000000-0000-4000-8000-${String(subjectIds.size + 1).padStart(12, '0')}`);
  return subjectIds.get(name)!;
};

export function buildReviewedWorld(now: number = Date.now()): MockWorld {
  const summaries = new Map<string, HomeworkSummary>();
  const details = new Map<string, HomeworkDetail>();
  reviewedHomework.forEach((item, index) => {
    const id = `10000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`;
    const summary: HomeworkSummary = {
      ...exampleSummary,
      id,
      title: item.title,
      subject: { id: subjectId(item.subject), name: item.subject },
      summary: item.title,
      deadline: { state: 'known', at: item.deadline, date_only: item.dateOnly },
      status: 'needs_clarification',
      urgency: 'normal',
      verification_state: item.verification,
      revision: 1,
      source: { kind: 'imported_history', imported: true, availability: { state: 'unavailable', reason: 'imported_without_link' } },
      processing: { state: 'none', retry_after_seconds: null },
      significant_update: false,
      significant_updated_at: null,
      my_state: { completion: 'pending', completed_at: null, completed_revision: null },
      created_at: item.createdAt,
      updated_at: item.updatedAt,
    };
    const detail: HomeworkDetail = {
      ...exampleDetailUnavailable,
      ...summary,
      description: item.title,
      source_detail: {
        kind: 'imported_history',
        imported: true,
        availability: { state: 'unavailable', reason: 'imported_without_link' },
        message_date: null,
        excerpt: null,
        action: { type: 'none', url: null },
      },
      generated_at: new Date(now).toISOString(),
    };
    summaries.set(id, summary);
    details.set(id, detail);
  });

  const today = dayKey(now, GROUP_TIMEZONE);
  const dueToday: string[] = [];
  const upcoming: string[] = [];
  for (const [id, item] of summaries) {
    const due = item.deadline.state === 'known' && item.deadline.at ? dayKey(item.deadline.at, GROUP_TIMEZONE) : null;
    if (due === today) dueToday.push(id);
    else if (due && due > today) upcoming.push(id);
  }
  return {
    summaries,
    details,
    sections: [
      ...(dueToday.length ? [{ kind: 'due_today' as const, ids: dueToday }] : []),
      ...(upcoming.length ? [{ kind: 'upcoming' as const, ids: upcoming.slice(0, 6) }] : []),
    ],
  };
}

type ScheduleTemplate = { weekday: number; start: string; end: string; subject: string; location: string | null };

const reviewedSchedule: ScheduleTemplate[] = [
  { weekday: 0, start: '09:00', end: '10:20', subject: 'Культура личности и общества', location: 'СДО' },
  { weekday: 0, start: '10:40', end: '12:00', subject: 'История России', location: 'СДО' },
  { weekday: 0, start: '12:20', end: '13:40', subject: 'Основы российской государственности', location: 'СДО' },
  { weekday: 0, start: '14:00', end: '15:20', subject: 'Основы российской государственности', location: 'Ауд. 502 · Вернадского, корп. 5' },
  { weekday: 1, start: '09:00', end: '10:20', subject: 'Русский язык как…', location: 'Зал №2/1 · Вернадского, корп. 1' },
  { weekday: 1, start: '10:40', end: '12:00', subject: 'Элективные дисциплины…', location: null },
  { weekday: 1, start: '12:20', end: '13:40', subject: 'История России', location: 'Ауд. 501 · Вернадского, корп. 5' },
  { weekday: 1, start: '15:40', end: '17:00', subject: 'Культура личности и общества', location: 'Ауд. 511 · Вернадского, корп. 5' },
  { weekday: 2, start: '09:00', end: '10:20', subject: 'Основы информатики', location: 'Ауд. 416 · Садовники 4, к.2' },
  { weekday: 2, start: '10:40', end: '12:00', subject: 'Математический анализ', location: 'Ауд. 421 · Садовники 4, к.2' },
  { weekday: 2, start: '12:20', end: '13:40', subject: 'Линейная алгебра и…', location: 'Ауд. 415 · Садовники 4, к.2' },
  { weekday: 3, start: '09:00', end: '10:20', subject: 'Линейная алгебра и…', location: 'Ауд. 402 · Садовники 4, к.2' },
  { weekday: 3, start: '10:40', end: '12:00', subject: 'Основы информатики', location: 'Ауд. 416 · Садовники 4, к.2' },
  { weekday: 3, start: '12:20', end: '13:40', subject: 'Математический анализ', location: 'Ауд. 421 · Садовники 4, к.2' },
  { weekday: 3, start: '15:40', end: '17:00', subject: 'История России', location: 'Зал №2/1 · Вернадского, корп. 1' },
  { weekday: 4, start: '09:00', end: '10:20', subject: 'Средства цифровой…', location: 'СДО' },
  { weekday: 4, start: '10:40', end: '12:00', subject: 'Математический анализ', location: 'СДО' },
  { weekday: 5, start: '10:40', end: '12:00', subject: 'Иностранный язык', location: 'Ауд. 102а · Вернадского, корп. 5' },
  { weekday: 5, start: '12:20', end: '13:40', subject: 'Иностранный язык', location: 'Ауд. 102а · Вернадского, корп. 5' },
  { weekday: 5, start: '14:00', end: '15:20', subject: 'Русский язык как…', location: 'Ауд. 3416 · Вернадского, корп. 1' },
];

export function buildReviewedSchedule(start: string, end: string, now: number): ScheduleResponse {
  const lessons: LessonOccurrence[] = [];
  for (let t = Date.parse(`${start}T00:00:00Z`); t <= Date.parse(`${end}T00:00:00Z`); t += DAY) {
    const date = new Date(t);
    const weekday = (date.getUTCDay() + 6) % 7;
    const key = date.toISOString().slice(0, 10);
    reviewedSchedule.filter((lesson) => lesson.weekday === weekday).forEach((lesson, index) => {
      lessons.push({
        id: `reviewed-${key}-${index}`,
        subject: lesson.subject,
        starts_at: `${key}T${lesson.start}:00+03:00`,
        ends_at: `${key}T${lesson.end}:00+03:00`,
        teacher: null,
        location: lesson.location,
        status: 'scheduled',
      });
    });
  }
  return {
    generated_at: new Date(now).toISOString(),
    group_timezone: GROUP_TIMEZONE,
    start,
    end,
    week_state: 'ready',
    first_week_anchor: '2026-10-05',
    selected_week: start < '2026-10-12' ? 'first' : 'second',
    lessons,
  };
}

export function buildReviewedNextLesson(now: number): NextLesson | null {
  const start = dayKey(now, GROUP_TIMEZONE);
  const end = new Date(Date.parse(`${start}T00:00:00Z`) + 7 * DAY).toISOString().slice(0, 10);
  const lessons = buildReviewedSchedule(start, end, now).lessons;
  const current = lessons.find((lesson) => Date.parse(lesson.starts_at) <= now && Date.parse(lesson.ends_at) > now);
  if (current) return { state: 'current', lesson: current };
  const upcoming = lessons.find((lesson) => Date.parse(lesson.starts_at) > now);
  return upcoming ? { state: 'upcoming', lesson: upcoming } : null;
}

export const reviewedDeadlines: AcademicDeadline[] = [
  { id: '30000000-0000-4000-8000-000000000001', kind: 'control_point', subject: 'Основы российской государственности', title: 'КТ по ОРГ: короткий тест', description: 'Короткий тест по ОРГ.', deadline_at: '2026-09-19T13:52:00Z', date_only: false, window_start: null, window_end: null, date_hint: null, needs_clarification: false, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000002', kind: 'assessment', subject: 'Математический анализ', title: 'Самостоятельная: функции и их свойства', description: 'Самостоятельная работа по функциям и их свойствам.', deadline_at: '2026-09-22T21:00:00Z', date_only: true, window_start: null, window_end: null, date_hint: null, needs_clarification: false, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000003', kind: 'test', subject: 'Основы российской государственности', title: 'Тест в СДО по ОРГ', description: 'Дата теста установлена; сообщение о возможном продлении не содержало новой даты.', deadline_at: '2026-09-23T13:52:00Z', date_only: false, window_start: null, window_end: null, date_hint: 'Возможное продление требует подтверждения.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000004', kind: 'control_point', subject: 'Русский язык как…', title: 'КТ 1: ударения', description: 'Контрольная точка по ударениям.', deadline_at: '2026-10-02T21:00:00Z', date_only: true, window_start: null, window_end: null, date_hint: null, needs_clarification: false, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000005', kind: 'control_point', subject: 'Основы российской государственности', title: 'КТ 1: эссе 1000–1500 слов', description: 'Эссе объёмом 1000–1500 слов.', deadline_at: null, date_only: false, window_start: '2026-10-09T21:00:00Z', window_end: '2026-10-15T21:00:00Z', date_hint: 'Период 10–15 октября; точный срок внутри периода требует уточнения.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000006', kind: 'test', subject: 'Иностранный язык', title: 'Тест в СДО и презентация', description: 'Тест в СДО и презентация.', deadline_at: '2026-10-09T21:00:00Z', date_only: true, window_start: null, window_end: null, date_hint: null, needs_clarification: false, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000007', kind: 'control_point', subject: 'Русский язык как…', title: 'КТ: пять конспектов разными методами', description: 'Пять лекций — пять разных методов.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Дата отчёта не указана.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000008', kind: 'control_point', subject: 'Русский язык как…', title: 'КТ 4: план и дизайн ПИР', description: 'План и дизайн ПИР.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Срок пока неизвестен.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000009', kind: 'control_point', subject: 'Русский язык как…', title: 'КТ 6: защита ПИР', description: 'Защита ПИР.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Ориентир — перед Новым годом; точная дата не подтверждена.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000010', kind: 'control_point', subject: 'Культура личности и общества', title: 'КТ: рефлексивный дневник', description: 'Записи несколько раз в неделю.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Конечный срок сдачи не указан.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000011', kind: 'control_point', subject: 'Средства цифровой…', title: 'КТ: первый тест, 30 баллов', description: 'Первый тест на 30 баллов.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Конец ноября 2026 года; точного дня нет.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000012', kind: 'control_point', subject: 'Средства цифровой…', title: 'КТ: второй тест, 30 баллов', description: 'Второй тест на 30 баллов.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Предположительно декабрь 2026 года; точного дня нет.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000013', kind: 'assessment', subject: 'Основы информатики', title: 'Контрольная работа по пройденным темам', description: 'Контрольная работа по пройденным темам.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: '«В следующий четверг»; точная дата требует уточнения.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000014', kind: 'assessment', subject: 'Линейная алгебра и…', title: 'Итоговая работа: до 30 баллов БРС', description: 'Итоговая работа до 30 баллов БРС.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Через 3–4 занятия; точная дата неизвестна.', needs_clarification: true, source_message_ids: [] },
  { id: '30000000-0000-4000-8000-000000000015', kind: 'assessment', subject: 'Математический анализ', title: 'Вторая самостоятельная: обратные функции, таблица и графики', description: 'Содержание известно, отдельная дата не указана.', deadline_at: null, date_only: false, window_start: null, window_end: null, date_hint: 'Дата самостоятельной не указана.', needs_clarification: true, source_message_ids: [] },
];

export function reviewedDeadlineArchived(item: AcademicDeadline, now: number): boolean {
  const last = item.deadline_at ?? item.window_end;
  if (!last) return false;
  if (item.date_only) return dayKey(last, GROUP_TIMEZONE) < dayKey(now, GROUP_TIMEZONE);
  return Date.parse(last) < now;
}
