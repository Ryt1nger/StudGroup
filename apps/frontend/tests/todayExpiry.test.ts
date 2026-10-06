import { expect, test, vi } from 'vitest';
import { buildToday, buildWorld } from '../src/mocks/data';

test('mock Today retains completed active deadlines but hides completed unknown/archived cards', () => {
  const world = buildWorld();
  const completed = [...world.summaries.values()].filter((item) => item.my_state.completion === 'completed');
  expect(completed.length).toBeGreaterThan(0);
  const ids = buildToday(world).sections.flatMap((section) => section.items.map((item) => item.id));
  for (const item of completed) {
    if (item.deadline.state === 'known' && Date.parse(item.deadline.at!) > Date.now()) {
      expect(ids).toContain(item.id);
    } else {
      expect(ids).not.toContain(item.id);
    }
    expect(world.details.has(item.id)).toBe(true);
  }
  for (const item of world.summaries.values()) {
    item.my_state = { ...item.my_state, completion: 'completed' };
  }
  const today = buildToday(world);
  expect(today.empty).toBe(false);
  expect(today.sections.every((section) => section.items.length > 0)).toBe(true);
  expect(today.sections.flatMap((s) => s.items).every((item) => item.deadline.state === 'known')).toBe(true);
});

test('mock Today excludes archived items immediately and preserves details', () => {
  vi.useFakeTimers();
  try {
    vi.setSystemTime(new Date('2026-10-05T06:00:00Z'));
    const now = Date.now();
    const world = buildWorld();
    const initial = buildToday(world, now);
    expect(initial.sections[0]!.kind).toBe('due_today');
    expect(initial.sections.some((section) => section.kind === 'overdue')).toBe(false);
    const item = initial.sections[0]!.items[0]!;
    const cutoff = Date.parse(item.deadline.at!);
    const ids = (at: number) => buildToday(world, at).sections.flatMap((s) => s.items.map((i) => i.id));
    expect(ids(cutoff - 1)).toContain(item.id);
    expect(ids(cutoff)).not.toContain(item.id);
    expect(world.details.has(item.id)).toBe(true);
  } finally {
    vi.useRealTimers();
  }
});
