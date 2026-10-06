import { expect, test } from 'vitest';
import { buildWorld } from '../src/mocks/data';
import { isHomeworkArchived } from '../src/lib/homeworkArchive';
import { selectHomework } from '../src/mocks/homeworkList';

const NOW = Date.parse('2026-10-05T06:00:00Z');
const TZ = 'Europe/Moscow';

test('archive starts at the exact timed deadline regardless of personal completion', () => {
  const world = buildWorld(NOW);
  const item = [...world.summaries.values()].find((card) => card.title === 'ДЗ №12–140')!;
  const cutoff = Date.parse(item.deadline.at!);
  expect(isHomeworkArchived(item, cutoff - 1, TZ)).toBe(false);
  expect(isHomeworkArchived(item, cutoff, TZ)).toBe(true);
  expect(selectHomework([item], 'all', cutoff)).toEqual([]);
  expect(selectHomework([item], 'archive', cutoff)).toEqual([item]);
  expect(item.my_state.completion).toBe('pending');
  expect(world.details.has(item.id)).toBe(true);
});

test('date-only archive begins at the next group midnight, not UTC midnight', () => {
  const item = [...buildWorld(NOW).summaries.values()][0]!;
  const dated = { ...item, deadline: { state: 'known' as const, at: '2026-10-05T00:00:00+03:00', date_only: true } };
  expect(isHomeworkArchived(dated, Date.parse('2026-10-05T20:59:59.999Z'), TZ)).toBe(false);
  expect(isHomeworkArchived(dated, Date.parse('2026-10-05T21:00:00Z'), TZ)).toBe(true);
});

test('cancellation archives immediately even without cancellation time; unknown deadlines stay active', () => {
  const item = [...buildWorld(NOW).summaries.values()][0]!;
  const unknown = { ...item, deadline: { state: 'unknown' as const, at: null, date_only: false } };
  expect(isHomeworkArchived(unknown, NOW, TZ)).toBe(false);
  const cancelled = { ...unknown, status: 'cancelled' as const, cancelled_at: null };
  expect(isHomeworkArchived(cancelled, NOW, TZ)).toBe(true);
  expect(selectHomework([cancelled], 'archive', NOW)).toEqual([cancelled]);
});
