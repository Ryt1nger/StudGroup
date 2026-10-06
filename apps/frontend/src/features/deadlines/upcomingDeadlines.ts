import type { AcademicDeadline } from '@studgroup/shared-types';
import { dayKey } from '../../lib/format';

/** Today preview: seven days, server time only; unknown dates stay in the full list. */
export function upcomingDeadlines(items: AcademicDeadline[], serverTime: string, timeZone: string): AcademicDeadline[] {
  const now = Date.parse(serverTime);
  if (!Number.isFinite(now)) return [];
  const horizon = now + 7 * 24 * 60 * 60 * 1000;
  return items.filter((item) => {
    if (item.deadline_at) {
      const at = Date.parse(item.deadline_at);
      if (!Number.isFinite(at) || at > horizon) return false;
      return item.date_only ? dayKey(at, timeZone) >= dayKey(now, timeZone) : at > now;
    }
    if (!item.window_start || !item.window_end) return false;
    const start = Date.parse(item.window_start);
    const end = Date.parse(item.window_end);
    return Number.isFinite(start) && Number.isFinite(end) && end > start && end > now && start <= horizon;
  });
}
