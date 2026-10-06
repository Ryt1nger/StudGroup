/** Dev/test-only implementation of the GET /homework rules (contract 0.3.0). Never imported by production code. */
import type { HomeworkSummary } from '@studgroup/shared-types';
import { dayKey, weekRange } from '../lib/format';
import { isHomeworkArchived } from '../lib/homeworkArchive';

export const GROUP_TZ = 'Europe/Moscow';
export const CURSOR_TTL_MS = 15 * 60_000;
export type ListFilter = 'all' | 'today' | 'week' | 'mine' | 'archive';
export const isListFilter = (v: string): v is ListFilter => v === 'all' || v === 'today' || v === 'week' || v === 'mine' || v === 'archive';

interface Cursor {
  f: ListFilter;
  /** offset into the frozen selection */
  o: number;
  /** frozen selection time (ms) */
  t: number;
}

/** Opaque to clients. */
export const encodeCursor = (c: Cursor): string => btoa(JSON.stringify(c)).replace(/=+$/, '');
export function decodeCursor(token: string): Cursor | null {
  try {
    const c = JSON.parse(atob(token)) as Cursor;
    return isListFilter(c.f) && Number.isInteger(c.o) && c.o >= 0 && Number.isFinite(c.t) ? c : null;
  } catch {
    return null;
  }
}

/** Server selection: active versus archive, filter, then created_at/id order. */
export function selectHomework(items: HomeworkSummary[], filter: ListFilter, nowMs: number): HomeworkSummary[] {
  const today = dayKey(nowMs, GROUP_TZ);
  const week = weekRange(nowMs, GROUP_TZ);
  const visible = items.filter((i) => isHomeworkArchived(i, nowMs, GROUP_TZ) === (filter === 'archive'));
  const completed = (i: HomeworkSummary) => i.my_state.completion === 'completed';
  const active = (i: HomeworkSummary) => !completed(i) && i.status !== 'cancelled';
  const known = (i: HomeworkSummary) => (i.deadline.state === 'known' && i.deadline.at ? i.deadline.at : null);
  const picked = visible.filter((i) => {
    switch (filter) {
      case 'all':
      case 'archive':
        return true;
      case 'mine':
        return completed(i) && i.status !== 'cancelled';
      case 'today': {
        const at = known(i);
        if (!active(i) || !at) return false;
        return dayKey(at, GROUP_TZ) === today;
      }
      case 'week': {
        const at = known(i);
        if (!active(i) || !at) return false;
        const d = dayKey(at, GROUP_TZ);
        return d >= week.start && d <= week.end;
      }
    }
  });
  return picked.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.id.localeCompare(b.id));
}
