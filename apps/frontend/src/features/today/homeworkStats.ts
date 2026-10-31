import type { HomeworkSummary } from '@studgroup/shared-types';

/** Counts the active, server-filtered personal list. Urgent is a subset of ahead. */
export function homeworkStats(items: readonly HomeworkSummary[]) {
  const unique = new Map(items.map((item) => [item.id, item]));
  const active = [...unique.values()].filter((item) => item.status !== 'cancelled');
  const done = active.filter((item) => item.my_state.completion === 'completed').length;
  const pending = active.filter((item) => item.my_state.completion !== 'completed');
  return {
    total: active.length,
    done,
    ahead: pending.length,
    urgent: pending.filter((item) => item.urgency !== 'normal').length,
  };
}
