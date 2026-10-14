import type { HomeworkSummary, SubjectRef } from '@studgroup/shared-types';

/** New API uses confirmed_subject; old responses safely fall back to their legacy subject. */
export function confirmedSubject(item: HomeworkSummary): SubjectRef | null {
  return Object.prototype.hasOwnProperty.call(item, 'confirmed_subject')
    ? (item.confirmed_subject ?? null)
    : item.subject;
}
