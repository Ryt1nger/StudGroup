import type { NextLesson } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { formatShortDate, formatTime, relativeDay } from '../../lib/format';
import type { NextClassView } from './viewModel';

function whenLabel(startsAt: string, nowMs: number, timeZone: string): string {
  const diffMin = Math.max(0, Math.ceil((Date.parse(startsAt) - nowMs) / 60_000));
  const rel = relativeDay(startsAt, nowMs, timeZone);
  if (rel === 'today') {
    if (diffMin < 60) return ru.nextLesson.inMinutes(Math.max(diffMin, 1));
    return ru.nextLesson.inHours(Math.floor(diffMin / 60), diffMin % 60);
  }
  if (rel === 'tomorrow') return ru.nextLesson.tomorrow;
  return formatShortDate(startsAt, timeZone);
}

/**
 * `today.next_lesson` → card props. The backend chooses the lesson and whether it is current or
 * upcoming (`state`); the client only formats. `null` means the card is not rendered at all.
 */
export function mapNextLesson(next: NextLesson | null | undefined, nowMs: number, timeZone: string): NextClassView | null {
  if (!next) return null;
  const { lesson } = next;
  const current = next.state === 'current';
  return {
    heading: current ? ru.nextLesson.headingCurrent : ru.nextLesson.headingUpcoming,
    whenLabel: current ? ru.nextLesson.whenCurrent : whenLabel(lesson.starts_at, nowMs, timeZone),
    timeRange: `${formatTime(lesson.starts_at, timeZone)} — ${formatTime(lesson.ends_at, timeZone)}`,
    subject: lesson.subject,
    details: [lesson.location, lesson.teacher].filter((p): p is string => Boolean(p)).join(' · '),
    badges: [], // the heading and the right-hand chip already say "now"
  };
}
