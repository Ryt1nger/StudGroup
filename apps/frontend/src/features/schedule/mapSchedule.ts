import type { LessonOccurrence, ScheduleResponse } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { dayKey, formatDayKey, formatTime } from '../../lib/format';
import { safeLessonUrl } from '../../ui/lessonLink';
import type { ScheduleDayView, ScheduleItemView, ScheduleScreenView } from './viewModel';

function lessonItem(
  lesson: LessonOccurrence,
  nowMs: number,
  timeZone: string,
  onOpen?: (lesson: LessonOccurrence) => void,
): ScheduleItemView {
  const start = Date.parse(lesson.starts_at);
  const end = Date.parse(lesson.ends_at);
  const current = start <= nowMs && nowMs < end;
  const done = end <= nowMs;
  // Only fields present in the contract are shown: no lesson type, no confirmed/moved badges.
  const subtitle = [lesson.location, lesson.teacher]
    .filter((part): part is string => Boolean(part))
    .join(' · ');
  const online =
    lesson.status !== 'cancelled' && end > nowMs ? safeLessonUrl(lesson.online_url) : null;
  return {
    id: lesson.id,
    time: formatTime(lesson.starts_at, timeZone),
    title: lesson.subject,
    subtitle,
    state: current ? 'current' : done ? 'done' : 'upcoming',
    cancelled: lesson.status === 'cancelled',
    onlineUrl: online,
    // «Сейчас» is only for online (СДО) lessons, where it opens the lesson; an in-person lesson in
    // progress is already marked by the highlighted card and the timeline node.
    badges: current && online ? [{ kind: 'now', label: ru.schedule.now, tone: 'solid', href: online }] : [],
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
 * group's calendar day in the order the backend returned them (starts_at, id); days are listed
 * chronologically and the current day is added empty when it has no lessons. The client does not
 * filter or infer anything else the contract does not state.
 */
export function mapSchedule(
  data: ScheduleResponse,
  nowMs: number,
  onOpen?: (lesson: LessonOccurrence) => void,
): ScheduleScreenView {
  const groups = new Map<string, ScheduleItemView[]>();
  for (const lesson of data.lessons) {
    const key = dayKey(lesson.starts_at, data.group_timezone);
    const items = groups.get(key) ?? [];
    items.push(lessonItem(lesson, nowMs, data.group_timezone, onOpen));
    groups.set(key, items);
  }
  // An empty current-day section is the entry-scroll anchor for a free day (see scheduleAnchor);
  // it does not invent lessons or lesson metadata.
  const today = dayKey(nowMs, data.group_timezone);
  if (today >= data.start && today <= data.end && !groups.has(today)) groups.set(today, []);
  const days: ScheduleDayView[] = [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, items]) => ({
      date: key,
      heading: dayHeading(key, nowMs, data.group_timezone),
      items,
    }));
  const weekLabel =
    data.selected_week === 'first'
      ? ru.schedule.weekFirst
      : data.selected_week === 'second'
        ? ru.schedule.weekSecond
        : undefined;
  return { weekLabel, days };
}
