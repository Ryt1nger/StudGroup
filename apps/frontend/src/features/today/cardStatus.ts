import type { HomeworkSummary } from '@studgroup/shared-types';

/** Single primary status label of a list card. Order is the priority order (first match wins). */
export type CardStatus = 'cancelled' | 'updated' | 'clarify' | 'completed';

type StatusInput = Pick<HomeworkSummary, 'status' | 'verification_state' | 'my_state' | 'significant_updated_at' | 'deadline'>;

/**
 * True only for a completed item whose last SIGNIFICANT update is later than the personal completion mark.
 * `revision` is a technical card version, not an importance signal, so it is deliberately not used.
 * The mark itself is never reset by the client.
 */
export function changedAfterCompletion(item: Pick<HomeworkSummary, 'my_state' | 'significant_updated_at'>): boolean {
  const { completion, completed_at: completedAt } = item.my_state;
  if (completion !== 'completed' || !completedAt || !item.significant_updated_at) return false;
  const updated = Date.parse(item.significant_updated_at);
  const completed = Date.parse(completedAt);
  return Number.isFinite(updated) && Number.isFinite(completed) && updated > completed;
}

export function needsClarification(item: Pick<HomeworkSummary, 'status' | 'verification_state'>): boolean {
  return item.status === 'needs_clarification' || item.verification_state === 'needs_clarification';
}

export function visibleClarification(item: Pick<HomeworkSummary, 'status' | 'verification_state' | 'deadline'>, isHeadman: boolean): boolean {
  return needsClarification(item) && (isHeadman || item.deadline.state !== 'known' || !item.deadline.at);
}

/**
 * Priority: cancelled > updated after completion > needs clarification > completed by me. Ordinary cards: null.
 *
 * Owner policy: known (including provisional) deadlines hide clarification from students;
 * headmen still see unresolved verification. Unknown deadlines warn every viewer.
 */
export function cardStatus(item: StatusInput, isHeadman = false): CardStatus | null {
  if (item.status === 'cancelled') return 'cancelled';
  if (changedAfterCompletion(item)) return 'updated';
  if (visibleClarification(item, isHeadman)) return 'clarify';
  if (item.my_state.completion === 'completed') return 'completed';
  return null;
}

/** «Срочно» comes only from `urgency` and is never shown for cancelled items. */
export function showUrgent(item: Pick<HomeworkSummary, 'urgency' | 'status'>): boolean {
  return item.urgency !== 'normal' && item.status !== 'cancelled';
}
