import type { AcademicDeadline } from '@studgroup/shared-types';
import { describe, expect, it } from 'vitest';
import { upcomingDeadlines } from '../src/features/deadlines/upcomingDeadlines';

const now = '2026-10-06T12:00:00+03:00';
const event = (patch: Partial<AcademicDeadline>): AcademicDeadline => ({
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', kind: 'control_point', subject: 'Предмет',
  title: 'КТ', description: 'Задача', deadline_at: null, date_only: false,
  window_start: null, window_end: null, date_hint: null, needs_clarification: false,
  source_message_ids: [], ...patch,
});

describe('Today upcoming deadlines', () => {
  it('excludes unknown, distant and passed deadlines, keeping the seven-day boundary', () => {
    const values = [
      event({ date_hint: 'В декабре' }),
      event({ deadline_at: '2026-11-01T12:00:00+03:00' }),
      event({ deadline_at: now }),
      event({ deadline_at: '2026-10-13T12:00:00+03:00' }),
    ];
    expect(upcomingDeadlines(values, now, 'Europe/Moscow')).toEqual([values[3]]);
  });
  it('keeps a date-only deadline through the group day', () => {
    const value = event({ deadline_at: '2026-10-06T00:00:00+03:00', date_only: true });
    expect(upcomingDeadlines([value], now, 'Europe/Moscow')).toEqual([value]);
    expect(upcomingDeadlines([value], '2026-10-07T00:00:00+03:00', 'Europe/Moscow')).toEqual([]);
  });
  it('keeps near and ongoing windows, excluding ended and distant windows', () => {
    const values = [
      event({ window_start: '2026-10-10T00:00:00+03:00', window_end: '2026-10-16T00:00:00+03:00' }),
      event({ window_start: '2026-10-01T00:00:00+03:00', window_end: '2026-10-07T00:00:00+03:00' }),
      event({ window_start: '2026-10-01T00:00:00+03:00', window_end: now }),
      event({ window_start: '2026-12-01T00:00:00+03:00', window_end: '2026-12-03T00:00:00+03:00' }),
    ];
    expect(upcomingDeadlines(values, now, 'Europe/Moscow')).toEqual(values.slice(0, 2));
  });
});
