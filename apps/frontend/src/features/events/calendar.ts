import type { EventPayload, PublishedEvent } from '@studgroup/shared-types';
import { dayKey, formatShortDate, formatTime } from '../../lib/format';

export function eventDays(item: EventPayload, timeZone: string): { first: string; last: string } | null {
  const first = item.starts_at ? dayKey(item.starts_at, timeZone) : item.starts_on;
  const last = item.ends_at
    ? dayKey(
        Date.parse(item.ends_at) -
          (item.starts_at && Date.parse(item.ends_at) <= Date.parse(item.starts_at) ? 0 : 1),
        timeZone,
      )
    : (item.ends_on ?? first);
  if (!first || !last || last < first || (item.multi_day && !item.ends_at && !item.ends_on)) return null;
  return { first, last };
}

export function groupEventOnDay(item: PublishedEvent, key: string, timeZone: string): boolean {
  const days = eventDays(item, timeZone);
  return item.state === 'active' && days !== null && days.first <= key && key <= days.last;
}

export function eventInHistory(item: PublishedEvent, nowMs: number, timeZone: string): boolean {
  if (item.state === 'cancelled') return true;
  if (item.ends_at) return Date.parse(item.ends_at) <= nowMs;
  const days = eventDays(item, timeZone);
  return days !== null && days.last < dayKey(nowMs, timeZone);
}

export function eventDate(item: EventPayload, timeZone: string): string {
  const days = eventDays(item, timeZone);
  if (!days) return item.when_text || 'Дата уточняется';
  const formatDay = (value: string) => formatShortDate(`${value}T12:00:00Z`, 'UTC');
  const first = `${formatDay(days.first)}${item.starts_at ? ` · ${formatTime(item.starts_at, timeZone)}` : ''}`;
  const shownEnd = item.ends_at ? dayKey(item.ends_at, timeZone) : days.last;
  if (shownEnd !== days.first)
    return `${first} — ${formatDay(shownEnd)}${item.ends_at ? ` · ${formatTime(item.ends_at, timeZone)}` : ''}`;
  return `${first}${item.ends_at ? ` – ${formatTime(item.ends_at, timeZone)}` : ''}`;
}
