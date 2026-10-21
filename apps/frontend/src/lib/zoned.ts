/** Wall-clock <-> instant conversion in an IANA timezone (the group's), without a date library. */

const PART_OPTIONS: Intl.DateTimeFormatOptions = {
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
};

function wallClock(ms: number, timeZone: string): Record<string, number> {
  const parts: Record<string, number> = {};
  for (const part of new Intl.DateTimeFormat('en-US', { timeZone, ...PART_OPTIONS }).formatToParts(
    new Date(ms),
  )) {
    if (part.type !== 'literal') parts[part.type] = Number(part.value);
  }
  return parts;
}

/** Offset (ms) of the timezone from UTC at the given instant. */
function offsetMs(ms: number, timeZone: string): number {
  const p = wallClock(ms, timeZone);
  const asUtc = Date.UTC(p.year!, p.month! - 1, p.day!, p.hour!, p.minute!, p.second!);
  return asUtc - Math.floor(ms / 1000) * 1000;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** `YYYY-MM-DD` and `HH:MM` of an instant as seen in the timezone. */
export function zonedParts(iso: string, timeZone: string): { date: string; time: string } {
  const p = wallClock(new Date(iso).getTime(), timeZone);
  return { date: `${p.year}-${pad(p.month!)}-${pad(p.day!)}`, time: `${pad(p.hour!)}:${pad(p.minute!)}` };
}

/** The UTC instant (ISO string) at which the wall clock of the timezone shows `date` `time`. */
export function zonedToIso(date: string, time: string, timeZone: string): string {
  const [y, m, d] = date.split('-').map(Number) as [number, number, number];
  const [hh, mm] = time.split(':').map(Number) as [number, number];
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  let ms = guess - offsetMs(guess, timeZone);
  // one more pass settles the instant when the guess fell on the other side of a DST change
  ms = guess - offsetMs(ms, timeZone);
  return new Date(ms).toISOString();
}
