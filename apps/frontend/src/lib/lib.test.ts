import { describe, expect, it } from 'vitest';
import { homeworkIdFromStartParam } from './deeplink';
import { formatDeadline, relativeDay } from './format';
import { isAllowedTelegramUrl } from './telegramUrl';

describe('isAllowedTelegramUrl', () => {
  it('accepts only https on the exact t.me host', () => {
    expect(isAllowedTelegramUrl('https://t.me/c/1234567890/42')).toBe(true);
    expect(isAllowedTelegramUrl('http://t.me/c/1/2')).toBe(false);
    expect(isAllowedTelegramUrl('https://t.me.evil.example/c/1/2')).toBe(false);
    expect(isAllowedTelegramUrl('https://telegram.me/x')).toBe(false);
    expect(isAllowedTelegramUrl('https://user:pw@t.me/x')).toBe(false);
    expect(isAllowedTelegramUrl('javascript:alert(1)')).toBe(false);
    expect(isAllowedTelegramUrl(null)).toBe(false);
  });
});

describe('homeworkIdFromStartParam', () => {
  it('restores hyphens for a valid hw_ parameter', () => {
    expect(homeworkIdFromStartParam('hw_44444444444444448444444444444444')).toBe('44444444-4444-4444-8444-444444444444');
  });
  it('rejects anything else', () => {
    expect(homeworkIdFromStartParam('hw_zzzz')).toBeNull();
    expect(homeworkIdFromStartParam('hw_4444444444444444844444444444444')).toBeNull();
    expect(homeworkIdFromStartParam('x_44444444444444448444444444444444')).toBeNull();
    expect(homeworkIdFromStartParam(null)).toBeNull();
  });
});

describe('deadline formatting', () => {
  const tz = 'Europe/Moscow';
  const now = Date.parse('2026-10-04T12:00:00Z');
  it('uses the group timezone for the relative day', () => {
    expect(relativeDay('2026-10-04T20:59:00Z', now, tz)).toBe('today');
    // 21:30Z is already the next day in Moscow (UTC+3)
    expect(relativeDay('2026-10-04T21:30:00Z', now, tz)).toBe('tomorrow');
  });
  it('shows time unless the deadline is date-only, and never invents unknown dates', () => {
    expect(formatDeadline({ state: 'known', at: '2026-10-04T20:59:00Z', date_only: false }, now, tz)).toBe('Сегодня · 23:59');
    expect(formatDeadline({ state: 'known', at: '2026-10-04T20:59:00Z', date_only: true }, now, tz)).toBe('Сегодня');
    expect(formatDeadline({ state: 'unknown', at: null, date_only: false }, now, tz)).toBeNull();
  });
});
