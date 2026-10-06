import type { HomeworkSummary } from '@studgroup/shared-types';
import { dayKey } from '../../lib/format';

export const TASK_FILTERS = ['all', 'today', 'week', 'mine', 'archive'] as const;
export type TaskFilter = (typeof TASK_FILTERS)[number];

export const TASK_SECTIONS = ['upcoming', 'unknown', 'done', 'overdue', 'cancelled'] as const;
// due_today is an internal filter category, displayed together with upcoming.
export type TaskSectionKind = (typeof TASK_SECTIONS)[number] | 'due_today';

export interface TaskSection {
  kind: TaskSectionKind;
  items: HomeworkSummary[];
}

export function parseTaskFilter(value: string | null): TaskFilter {
  return (TASK_FILTERS as readonly string[]).includes(value ?? '') ? (value as TaskFilter) : 'all';
}

/**
 * Where one item belongs in the full list. `nowMs` must be server-corrected and days are the group's
 * calendar days. Priority: cancelled > completed by me > unknown deadline > overdue > due today > upcoming.
 */
export function classifyTask(item: HomeworkSummary, nowMs: number, timeZone: string): TaskSectionKind {
  if (item.status === 'cancelled') return 'cancelled';
  if (item.my_state.completion === 'completed') return 'done';
  const at = item.deadline.state === 'known' ? item.deadline.at : null;
  if (!at) return 'unknown';
  const deadlineDay = dayKey(at, timeZone);
  const today = dayKey(nowMs, timeZone);
  // A date-only deadline lasts until the end of that calendar day.
  const passed = item.deadline.date_only ? deadlineDay < today : Date.parse(at) < nowMs;
  if (passed) return 'overdue';
  return deadlineDay === today ? 'due_today' : 'upcoming';
}

const time = (iso: string | null | undefined) => (iso ? Date.parse(iso) : 0);

function sortItems(kind: TaskSectionKind, items: HomeworkSummary[]): HomeworkSummary[] {
  const by = (key: (i: HomeworkSummary) => number, dir: 1 | -1) => [...items].sort((a, b) => dir * (key(a) - key(b)) || a.title.localeCompare(b.title, 'ru'));
  switch (kind) {
    case 'overdue':
      return by((i) => time(i.deadline.at), -1); // most recently overdue first
    case 'due_today':
    case 'upcoming':
      return by((i) => time(i.deadline.at), 1);
    case 'done':
      return by((i) => time(i.my_state.completed_at), -1);
    default:
      return by((i) => time(i.updated_at), -1);
  }
}

/**
 * Merges cursor pages into one list keyed by id. A repeated id keeps its first position but takes the
 * newest data (the list is not an immutable snapshot, so later pages may carry fresher state).
 */
export function mergePages(pages: ReadonlyArray<{ items: readonly HomeworkSummary[] }>): HomeworkSummary[] {
  const byId = new Map<string, HomeworkSummary>();
  for (const page of pages) for (const item of page.items) byId.set(item.id, item);
  return [...byId.values()];
}

/**
 * Groups the items returned by the server into the approved sections. The server's filter
 * (all/today/week/mine) is authoritative: nothing is re-filtered here by date or completion.
 * Archive membership is selected by the server; no cancellation expiry applies in the archive.
 * Sections without items are not returned, so their headings are never rendered.
 */
export function groupTaskSections(items: readonly HomeworkSummary[], nowMs: number, timeZone: string): TaskSection[] {
  const groups = new Map<TaskSectionKind, HomeworkSummary[]>();
  for (const item of items) {
    const kind = classifyTask(item, nowMs, timeZone);
    const sectionKind = kind === 'due_today' ? 'upcoming' : kind;
    groups.set(sectionKind, [...(groups.get(sectionKind) ?? []), item]);
  }
  return TASK_SECTIONS.filter((kind) => groups.has(kind)).map((kind) => ({ kind, items: sortItems(kind, groups.get(kind)!) }));
}
