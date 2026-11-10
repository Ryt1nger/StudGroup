import { describe, expect, it } from 'vitest';
import type { EventPayload } from '@studgroup/shared-types';
import { eventDays } from './calendar';
import { eventPayload } from './eventPayload';

describe('group event calendar date flow', () => {
  const withoutDate = { title: 'Встреча группы', description: 'В актовом зале', when_text: 'Дата уточняется', audience: 'group' } as EventPayload;

  it('does not put undated events on a calendar day', () => {
    expect(eventDays(withoutDate, 'Europe/Moscow')).toBeNull();
  });

  it('puts a dated event on exactly the chosen local day', () => {
    const dated = { ...eventPayload(withoutDate), starts_on: '2026-11-12', when_text: null };
    expect(eventDays(dated, 'Europe/Moscow')).toEqual({ first: '2026-11-12', last: '2026-11-12' });
  });

  it('preserves real event fields but never copies response metadata into a write', () => {
    const untrusted = { ...withoutDate, id: 'not-a-payload', revision: 30, source_url: 'https://example.com', place: 'Аудитория 101' };
    const safe = eventPayload(untrusted);
    expect(safe.title).toBe('Встреча группы');
    expect(safe.place).toBe('Аудитория 101');
    expect(safe).not.toHaveProperty('id');
    expect(safe).not.toHaveProperty('revision');
    expect(safe).not.toHaveProperty('source_url');
  });
});
