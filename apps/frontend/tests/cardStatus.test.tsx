import type { HomeworkSummary } from '@studgroup/shared-types';
import { describe, expect, it } from 'vitest';
import { cardStatus, changedAfterCompletion, showUrgent } from '../src/features/today/cardStatus';


function item(patch: Partial<HomeworkSummary> = {}): HomeworkSummary {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    type: 'homework',
    title: 'ДЗ №5',
    subject: { id: 's1', name: 'Физика' },
    summary: null,
    deadline: { state: 'known', at: '2026-10-07T20:59:00Z', date_only: false },
    status: 'published',
    urgency: 'normal',
    visibility: 'group',
    verification_state: 'manual_confirmed',
    revision: 2,
    source: { kind: 'telegram_group_message', imported: true, availability: { state: 'available' } },
    processing: { state: 'reanalyzing', retry_after_seconds: null },
    significant_update: false,
    significant_updated_at: null,
    my_state: { completion: 'pending', completed_at: null, completed_revision: null },
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    ...patch,
  } as HomeworkSummary;
}
const done = (rev: number) => ({ completion: 'completed', completed_at: '2026-10-02T10:00:00Z', completed_revision: rev }) as HomeworkSummary['my_state'];
const AFTER = '2026-10-03T10:00:00Z';
const BEFORE = '2026-10-01T10:00:00Z';

describe('cardStatus priority', () => {
  it('ordinary card has no status', () => {
    expect(cardStatus(item())).toBeNull();
  });

  it('cancelled beats everything', () => {
    const i = item({ status: 'cancelled', verification_state: 'needs_clarification', my_state: done(1), urgency: 'urgent' });
    expect(cardStatus(i)).toBe('cancelled');
    expect(showUrgent(i)).toBe(false);
  });

  it('updated-after-completion beats clarification and completed', () => {
    const i = item({ significant_updated_at: AFTER, verification_state: 'needs_clarification', my_state: done(1) });
    expect(cardStatus(i)).toBe('updated');
  });

  it('revision alone never triggers «updated»: it is a technical version', () => {
    const i = item({ revision: 9, my_state: done(1), significant_updated_at: null });
    expect(changedAfterCompletion(i)).toBe(false);
    expect(cardStatus(i)).toBe('completed');
  });

  it('a significant update older than the completion mark is not «updated»', () => {
    const i = item({ revision: 9, my_state: done(1), significant_updated_at: BEFORE });
    expect(changedAfterCompletion(i)).toBe(false);
    expect(cardStatus(i)).toBe('completed');
  });

  it('a significant update later than the completion mark is «updated» regardless of revision', () => {
    const i = item({ revision: 1, my_state: done(1), significant_updated_at: AFTER });
    expect(changedAfterCompletion(i)).toBe(true);
    expect(cardStatus(i)).toBe('updated');
  });

  it('missing timestamps never claim a change', () => {
    expect(changedAfterCompletion(item({ significant_updated_at: AFTER, my_state: { completion: 'completed', completed_at: null } as HomeworkSummary['my_state'] }))).toBe(false);
  });

  it('a pending item is never «updated»', () => {
    expect(cardStatus(item({ significant_updated_at: AFTER, revision: 9 }))).toBeNull();
  });

  it('headman sees clarification for a known deadline, student sees completion', () => {
    const value = item({ status: 'needs_clarification', my_state: done(2) });
    expect(cardStatus(value, true)).toBe('clarify');
    expect(cardStatus(value, false)).toBe('completed');
  });

  it('unknown deadline does not suppress the clarification label (no uncertain_fields yet)', () => {
    expect(cardStatus(item({ status: 'needs_clarification', deadline: { state: 'unknown', at: null, date_only: false } }))).toBe('clarify');
  });

  it('urgency is shown only from the field', () => {
    expect(showUrgent(item({ urgency: 'super_urgent' }))).toBe(true);
    expect(showUrgent(item({ urgency: 'normal' }))).toBe(false);
  });
});
