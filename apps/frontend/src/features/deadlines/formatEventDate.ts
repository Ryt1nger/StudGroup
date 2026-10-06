import type { AcademicDeadline } from '@studgroup/shared-types';
import { formatShortDate, formatTime } from '../../lib/format';

export function formatEventDate(item: AcademicDeadline, timeZone: string): string {
  if (item.deadline_at) {
    return `${formatShortDate(item.deadline_at, timeZone)}${item.date_only ? '' : ` · ${formatTime(item.deadline_at, timeZone)}`}`;
  }
  if (item.window_start && item.window_end) {
    const start = Date.parse(item.window_start);
    const end = Date.parse(item.window_end);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      // Stored end is exclusive: midnight 16 Oct means the displayed period ends 15 Oct.
      return `${formatShortDate(item.window_start, timeZone)} — ${formatShortDate(new Date(end - 1).toISOString(), timeZone)}`;
    }
  }
  return 'Дата уточняется';
}
