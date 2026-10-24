import type { LessonOccurrence } from '@studgroup/shared-types';

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
