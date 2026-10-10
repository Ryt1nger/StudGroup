import type { LessonOccurrence, ScheduleResponse } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { dayKey, formatDayKey, formatTime } from '../../lib/format';
import type { ScheduleDayView, ScheduleItemView, ScheduleScreenView } from './viewModel';

function lessonItem(lesson: LessonOccurrence, nowMs: number, timeZone: string, onOpen?: (lesson: LessonOccurrence) => void): ScheduleItemView {
  const start = Date.parse(lesson.starts_at);
  const end = Date.parse(lesson.ends_at);
  const current = start <= nowMs && nowMs < end;
  const done = end <= nowMs;
  // Only fields present in the contract are shown: no lesson type, no confirmed/moved badges.
  const subtitle = [lesson.location, lesson.teacher].filter((part): part is string => Boolean(part)).join(' · ');
  return {
    id: lesson.id,
    time: formatTime(lesson.starts_at, timeZone),
    title: lesson.subject,
    subtitle,
    state: current ? 'current' : done ? 'done' : 'upcoming',
    badges: current ? [{ kind: 'now', label: ru.schedule.now, tone: 'solid' }] : [],
    onOpen: onOpen ? () => onOpen(lesson) : undefined,
  };
}

function dayHeading(key: string, nowMs: number, timeZone: string): string {
  const label = formatDayKey(key);
  const today = dayKey(nowMs, timeZone);
  if (key === today) return `${ru.schedule.today}, ${label.split(', ')[1]}`;
  const tomorrow = dayKey(nowMs + 24 * 3_600_000, timeZone);
  if (key === tomorrow) return `${ru.schedule.tomorrow}, ${label.split(', ')[1]}`;
  return label;
}

/**
 * Maps the generated `ScheduleResponse` to display-ready props. Lessons are grouped by the
 * group's calendar day in the order the backend returned them (starts_at, id); the client does
 * not re-sort, filter or infer anything the contract does not state.
 */
export function mapSchedule(data: ScheduleResponse, nowMs: number, onOpen?: (lesson: LessonOccurrence) => void): ScheduleScreenView {
  const groups = new Map<string, ScheduleItemView[]>();
  for (const lesson of data.lessons) {
    const key = dayKey(lesson.starts_at, data.group_timezone);
    const items = groups.get(key) ?? [];
    items.push(lessonItem(lesson, nowMs, data.group_timezone, onOpen));
    groups.set(key, items);
  }
  const days: ScheduleDayView[] = [...groups.entries()].map(([key, items]) => ({
    heading: dayHeading(key, nowMs, data.group_timezone),
    items,
  }));
  const weekLabel =
    data.selected_week === 'first' ? ru.schedule.weekFirst : data.selected_week === 'second' ? ru.schedule.weekSecond : undefined;
  return { weekLabel, days };
}
