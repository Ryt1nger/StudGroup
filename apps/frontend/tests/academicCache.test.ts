import { describe, expect, it } from 'vitest';
import { academicCacheOwner, canCacheAcademicQuery } from '../src/api/academicCache';
import { exampleSession } from '../src/mocks/data';

describe('encrypted academic cache allowlist', () => {
  it('accepts only safe read-only academic views', () => {
    for (const key of [
      ['today', 'today'], ['today', 'tomorrow'], ['schedule', '2026-11-02', '2026-11-08'],
      ['academic-deadlines', false, false], ['homework-counts'], ['subjects'],
      ['group-events', 'list', { start: '2026-11-02', end: '2026-11-08' }],
    ]) expect(canCacheAcademicQuery(key)).toBe(true);
  });

  it('never persists credentials, user profiles, private details or staff drafts', () => {
    for (const key of [
      ['profile'], ['notifications'], ['session'], ['attachment-image', 'x'],
      ['homework', 'x'], ['homework-list', 'all'], ['academic-deadlines', true, false],
      ['group-events', 'review'], ['group-events', 'list', { history: true }],
      ['schedule', '../private', '2026-11-08'], ['today', 'unknown'],
    ]) expect(canCacheAcademicQuery(key)).toBe(false);
  });

  it('requires an active server-confirmed membership and scopes role changes', () => {
    const session = exampleSession.session;
    const owner = academicCacheOwner(session);
    expect(owner).toBeTruthy();
    expect(academicCacheOwner({ ...session, access_state: 'no_active_group' })).toBeNull();
    expect(academicCacheOwner({ ...session, membership: null })).toBeNull();
    expect(academicCacheOwner({
      ...session,
      membership: session.membership ? { ...session.membership, role: 'deputy' } : null,
    })).not.toBe(owner);
  });
});
