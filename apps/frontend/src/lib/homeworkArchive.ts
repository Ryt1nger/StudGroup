import type { HomeworkSummary } from '@studgroup/shared-types';
import { dayKey } from './format';

/** Derived archive membership; never changes completion or deletes data. */
export function isHomeworkArchived(item: HomeworkSummary, nowMs: number, timeZone: string): boolean {
  if (item.status === 'cancelled') return true;
  if (item.deadline.state !== 'known' || !item.deadline.at) return false;
  return item.deadline.date_only
    ? dayKey(item.deadline.at, timeZone) < dayKey(nowMs, timeZone)
    : Date.parse(item.deadline.at) <= nowMs;
}
