import type { AcademicDeadline } from '@studgroup/shared-types';
import { addDays, dayKey } from '../../lib/format';

/** «Сегодня» shows only events due today or tomorrow, by server time; unknown dates stay in the full list. */
export function upcomingDeadlines(items: AcademicDeadline[], serverTime: string, timeZone: string): AcademicDeadline[] {
  const now = Date.parse(serverTime);
  if (!Number.isFinite(now)) return [];
  const today = dayKey(now, timeZone);
  const last = addDays(today, 1);
  return items.filter((item) => {
    if (item.deadline_at) {
      const at = Date.parse(item.deadline_at);
      if (!Number.isFinite(at)) return false;
      const day = dayKey(at, timeZone);
      if (day > last) return false;
      return item.date_only ? day >= today : at > now;
    }
    if (!item.window_start || !item.window_end) return false;
    const start = Date.parse(item.window_start);
    const end = Date.parse(item.window_end);
    return Number.isFinite(start) && Number.isFinite(end) && end > start && end > now && dayKey(start, timeZone) <= last;
  });
}
