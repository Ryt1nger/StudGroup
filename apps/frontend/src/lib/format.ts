const LOCALE = 'ru-RU';

/** Calendar day key (YYYY-MM-DD) of an instant in the given IANA timezone. */
export function dayKey(iso: string | number | Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    new Date(iso),
  );
  return parts;
}

function addDays(key: string, days: number): string {
  const [y, m, d] = key.split('-').map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

export function formatTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(LOCALE, { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso));
}

export function formatShortDate(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(LOCALE, { timeZone, day: 'numeric', month: 'short' }).format(new Date(iso)).replace('.', '');
}

/** "4 октября, воскресенье" for the screen subtitle. */
export function formatLongDay(iso: string, timeZone: string): string {
  const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(LOCALE, { timeZone, ...o }).format(new Date(iso));
  return `${fmt({ day: 'numeric', month: 'long' })}, ${fmt({ weekday: 'long' })}`;
}

export interface DayParts {
  day: string;
  month: string;
}
/** "02" / "ОКТ" parts for the date tile. */
export function formatDateTile(iso: string, timeZone: string): DayParts {
  const day = new Intl.DateTimeFormat(LOCALE, { timeZone, day: '2-digit' }).format(new Date(iso));
  const month = new Intl.DateTimeFormat(LOCALE, { timeZone, month: 'short' }).format(new Date(iso)).replace('.', '');
  return { day, month: month.toUpperCase() };
}

export type RelativeDay = 'today' | 'tomorrow' | 'other';

export function relativeDay(iso: string, nowMs: number, timeZone: string): RelativeDay {
  const target = dayKey(iso, timeZone);
  const today = dayKey(nowMs, timeZone);
  if (target === today) return 'today';
  if (target === addDays(today, 1)) return 'tomorrow';
  return 'other';
}

export interface DeadlineInput {
  state: 'known' | 'unknown';
  at?: string | null;
  date_only: boolean;
}

/**
 * Deadline label. `nowMs` must be server-corrected time. Never invents a date for unknown deadlines;
 * the caller decides how to show the unknown state (it has its own copy).
 */
export function formatDeadline(deadline: DeadlineInput, nowMs: number, timeZone: string): string | null {
  if (deadline.state !== 'known' || !deadline.at) return null;
  const rel = relativeDay(deadline.at, nowMs, timeZone);
  const dayLabel = rel === 'today' ? 'Сегодня' : rel === 'tomorrow' ? 'Завтра' : formatShortDate(deadline.at, timeZone);
  if (deadline.date_only) return dayLabel;
  return `${dayLabel} · ${formatTime(deadline.at, timeZone)}`;
}

/** Monday..Sunday (inclusive YYYY-MM-DD keys) of the week containing `nowMs` in the given timezone. */
export function weekRange(nowMs: number, timeZone: string): { start: string; end: string } {
  const today = dayKey(nowMs, timeZone);
  const [y, m, d] = today.split('-').map(Number) as [number, number, number];
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 = Sunday
  const sinceMonday = (weekday + 6) % 7;
  return { start: addDays(today, -sinceMonday), end: addDays(today, 6 - sinceMonday) };
}

/** "Пятница, 2 октября" for a YYYY-MM-DD key. */
export function formatDayKey(key: string): string {
  const [y, m, d] = key.split('-').map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d, 12));
  const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(LOCALE, { timeZone: 'UTC', ...o }).format(date);
  const weekday = fmt({ weekday: 'long' });
  return `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)}, ${fmt({ day: 'numeric', month: 'long' })}`;
}

export { addDays };
