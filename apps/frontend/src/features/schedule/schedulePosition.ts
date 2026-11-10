import type { LessonOccurrence } from '@studgroup/shared-types';

/**
 * A timetable is positioned by clock time, but a short lesson or a long teacher/location
 * label can need more height than its nominal slot. Extend the time axis *after* each
 * overflowing lesson so subsequent cards and hour markers never cover its text.
 */
export interface ScheduleCardSize {
  startMinute: number;
  endMinute: number;
  height: number;
}

export interface ScheduleExpansion {
  endMinute: number;
  extra: number;
}

export function scheduleExpansions(cards: readonly ScheduleCardSize[], pxPerHour: number): ScheduleExpansion[] {
  return cards
    .filter((card) => card.endMinute > card.startMinute)
    .map((card) => ({
      endMinute: card.endMinute,
      // Reserve a small gap before the next lesson even for a very short slot.
      extra: Math.max(0, card.height + 4 - ((card.endMinute - card.startMinute) * pxPerHour) / 60),
    }))
    .filter((item) => item.extra > 0.5)
    .sort((a, b) => a.endMinute - b.endMinute);
}

export function scheduleY(minute: number, pxPerHour: number, expansions: readonly ScheduleExpansion[]): number {
  return (minute * pxPerHour) / 60 +
    expansions.reduce((offset, item) => offset + (item.endMinute <= minute ? item.extra : 0), 0);
}

/** True while the current-time line geometrically crosses a lesson card. */
export function lessonAt(lessons: readonly LessonOccurrence[], nowMs: number): boolean {
  return lessons.some((lesson) => {
    const start = Date.parse(lesson.starts_at);
    const end = Date.parse(lesson.ends_at);
    return start <= nowMs && nowMs < end;
  });
}

/**
 * Position the schedule on the useful point when its screen opens.
 * The lesson in progress wins; otherwise today follows the live line, then the next/last lesson.
 */
export function positionSchedule(scroll: HTMLElement, nowMs: number): void {
  const pinned = scroll.querySelector<HTMLElement>('.pinned')?.offsetHeight ?? 0;
  const events = [...scroll.querySelectorAll<HTMLElement>('.ev')];
  const current = events.find(
    (event) => Number(event.dataset.start) <= nowMs && nowMs < Number(event.dataset.end),
  );
  if (current) {
    const y =
      current.getBoundingClientRect().top -
      scroll.getBoundingClientRect().top +
      scroll.scrollTop -
      pinned -
      20;
    scroll.scrollTop = Math.max(0, y);
    return;
  }

  const line = scroll.querySelector<HTMLElement>('.nowline');
  if (line) {
    const y =
      line.getBoundingClientRect().top -
      scroll.getBoundingClientRect().top +
      scroll.scrollTop -
      pinned -
      140;
    scroll.scrollTop = Math.max(0, y);
    return;
  }

  const target =
    events.find((event) => Number(event.dataset.start) > nowMs) ?? events[events.length - 1];
  if (!target) return;
  const y =
    target.getBoundingClientRect().top -
    scroll.getBoundingClientRect().top +
    scroll.scrollTop -
    pinned -
    40;
  scroll.scrollTop = Math.max(0, y);
}
