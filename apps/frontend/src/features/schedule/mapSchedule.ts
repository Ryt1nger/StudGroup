import type { LessonOccurrence, ScheduleResponse } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { dayKey, formatDayKey, formatTime } from '../../lib/format';
import type { ScheduleDayView, ScheduleItemView, ScheduleScreenView } from './viewModel';

function lessonItem(lesson: LessonOccurrence, nowMs: number, timeZone: string): ScheduleItemView {
  const start = Date.parse(lesson.starts_at);
  const end = Date.parse(lesson.ends_at);
  const cancelled = lesson.status === 'cancelled';
  const current = !cancelled && start <= nowMs && nowMs < end;
  const done = end <= nowMs;
  // Only fields present in the contract are shown: no lesson type, no confirmed/moved badges.
  const subtitle = [lesson.location, lesson.teacher].filter((part): part is string => Boolean(part)).join(' · ');
  return {
    id: lesson.id,
    time: formatTime(lesson.starts_at, timeZone),
    title: lesson.subject,
    subtitle,
    cancelled,
    onlineUrl: !cancelled && !done ? lesson.online_url : null,
    state: current ? 'current' : done ? 'done' : 'upcoming',
    badges: cancelled ? [{ kind: 'cancelled', label: 'Отменено', tone: 'neutral' }] : current ? [{ kind: 'now', label: ru.schedule.now, tone: 'solid' }] : [],
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
 * Groups contract lessons by the group's calendar day, preserving backend lesson order.
 * An empty current-day section is inserted chronologically as an entry-scroll anchor;
 * it does not invent lessons or lesson metadata.
 */
export function mapSchedule(data: ScheduleResponse, nowMs: number): ScheduleScreenView {
  const groups = new Map<string, ScheduleItemView[]>();
  for (const lesson of data.lessons) {
    const key = dayKey(lesson.starts_at, data.group_timezone);
    const items = groups.get(key) ?? [];
    items.push(lessonItem(lesson, nowMs, data.group_timezone));
    groups.set(key, items);
  }
  const today = dayKey(nowMs, data.group_timezone);
  if (today >= data.start && today <= data.end && !groups.has(today)) groups.set(today, []);
  const days: ScheduleDayView[] = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, items]) => ({
    date: key,
    heading: dayHeading(key, nowMs, data.group_timezone),
    items,
  }));
  const weekLabel =
    data.selected_week === 'first' ? ru.schedule.weekFirst : data.selected_week === 'second' ? ru.schedule.weekSecond : undefined;
  return { weekLabel, days };
}
