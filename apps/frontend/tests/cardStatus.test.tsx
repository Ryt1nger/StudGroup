import type { HomeworkSummary } from '@studgroup/shared-types';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { HomeworkCard } from '../src/features/today/HomeworkCard';
import { cardStatus, changedAfterCompletion, showUrgent } from '../src/features/today/cardStatus';
import { TelegramProvider } from '../src/telegram/TelegramProvider';
import { makeAdapter } from './helpers';

const NOW = Date.parse('2026-10-05T09:00:00Z');
const TZ = 'Europe/Moscow';

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

function renderCard(i: HomeworkSummary, archived = false) {
  return render(
    <TelegramProvider adapter={makeAdapter()}>
      <MemoryRouter>
        <HomeworkCard item={i} nowMs={NOW} timeZone={TZ} archived={archived} />
      </MemoryRouter>
    </TelegramProvider>,
  );
}
const LABELS = ['Отменено', 'Обновлено — проверьте', 'Требует уточнения', 'Выполнено мной'];
const shown = () => LABELS.filter((l) => screen.queryByText(l));

describe('archive card presentation', () => {
  it('hides urgent and updated labels, retaining only an accessible completion indicator', () => {
    renderCard(item({ urgency: 'urgent', my_state: done(1), significant_updated_at: AFTER }), true);
    expect(shown()).toEqual([]);
    expect(screen.queryByText('Срочно')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Выполнено мной' })).toBeInTheDocument();
  });

  it('hides cancellation badges without falsely marking the item completed', () => {
    renderCard(item({ status: 'cancelled' }), true);
    expect(shown()).toEqual([]);
    expect(screen.queryByRole('img', { name: 'Выполнено мной' })).not.toBeInTheDocument();
  });
});

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

describe('HomeworkCard rendering', () => {
  it('ordinary card: title, subject, deadline and nothing else; no ДЗ chip, source, import or processing', () => {
    renderCard(item());
    expect(screen.getByText('ДЗ №5')).toBeInTheDocument();
    expect(screen.getByText('Физика')).toBeInTheDocument();
    expect(screen.getByText(/8 окт|Завтра|7 окт/)).toBeInTheDocument();
    expect(shown()).toEqual([]);
    for (const hidden of ['ДЗ', 'Из группы', 'Импортировано', 'Подтверждено', 'Проверяем обновление', 'Срочно']) {
      expect(screen.queryByText(hidden)).not.toBeInTheDocument();
    }
  });

  it('shows exactly one primary label for every combination (no duplicates)', () => {
    const combos: HomeworkSummary[] = [
      item({ status: 'cancelled', my_state: done(1), verification_state: 'needs_clarification' }),
      item({ significant_updated_at: AFTER, my_state: done(1), status: 'needs_clarification' }),
      item({ status: 'needs_clarification', my_state: done(2) }),
      item({ my_state: done(2) }),
    ];
    const expected = ['Отменено', 'Обновлено — проверьте', 'Выполнено мной', 'Выполнено мной'];
    combos.forEach((c, idx) => {
      const { unmount } = renderCard(c);
      expect(shown()).toEqual([expected[idx]]);
      expect(screen.getAllByText(expected[idx] as string)).toHaveLength(1);
      unmount();
    });
  });

  it('urgent adds «Срочно» next to one primary label, but never on a cancelled card', () => {
    const { unmount } = renderCard(item({ urgency: 'urgent', my_state: done(2) }));
    expect(shown()).toEqual(['Выполнено мной']);
    expect(screen.getAllByText('Срочно')).toHaveLength(1);
    unmount();
    renderCard(item({ urgency: 'super_urgent', status: 'cancelled' }));
    expect(shown()).toEqual(['Отменено']);
    expect(screen.queryByText('Срочно')).not.toBeInTheDocument();
  });

  it('unknown deadline: «Срок уточняется» in the date line AND the «Требует уточнения» label, once each', () => {
    renderCard(item({ status: 'needs_clarification', deadline: { state: 'unknown', at: null, date_only: false } }));
    expect(screen.getAllByText('Срок уточняется')).toHaveLength(1);
    expect(screen.getAllByText('Требует уточнения')).toHaveLength(1);
    expect(shown()).toEqual(['Требует уточнения']);
  });

  it('unknown deadline without clarification state shows only the date line', () => {
    renderCard(item({ deadline: { state: 'unknown', at: null, date_only: false } }));
    expect(screen.getAllByText('Срок уточняется')).toHaveLength(1);
    expect(shown()).toEqual([]);
  });

  it('hides the clarification warning from a student when the deadline is known', () => {
    renderCard(item({ verification_state: 'needs_clarification' }));
    expect(screen.queryByText('Требует уточнения')).not.toBeInTheDocument();
  });

  it('card background does not depend on completion', () => {
    const a = renderCard(item());
    const clsA = a.container.querySelector('a')!.className;
    a.unmount();
    const b = renderCard(item({ my_state: done(2) }));
    expect(b.container.querySelector('a')!.className).toBe(clsA);
  });
});
