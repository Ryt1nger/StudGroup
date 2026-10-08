import type { ScheduleResponse } from '@studgroup/shared-types';
import { useEffect, useRef } from 'react';
import { dayKey } from '../../lib/format';

export const dayAnchorId = (date: string) => `schedule-day-${date}`;
export const lessonAnchorId = (id: string) => `schedule-lesson-${encodeURIComponent(id)}`;

export function scheduleAnchor(data: ScheduleResponse, nowMs: number): string | undefined {
  const today = dayKey(nowMs, data.group_timezone);
  if (today < data.start || today > data.end) return undefined;
  const lessons = data.lessons.filter((lesson) => lesson.status !== 'cancelled' && dayKey(lesson.starts_at, data.group_timezone) === today);
  const current = lessons.find((lesson) => Date.parse(lesson.starts_at) <= nowMs && nowMs < Date.parse(lesson.ends_at));
  const next = lessons.find((lesson) => Date.parse(lesson.starts_at) > nowMs);
  return current || next ? lessonAnchorId((current ?? next)!.id) : dayAnchorId(today);
}

export function useScheduleEntryAnchor(initialAnchorId?: string) {
  const anchored = useRef(false);
  useEffect(() => {
    if (!initialAnchorId || anchored.current) return;
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(initialAnchorId);
      if (!target) return;
      target.scrollIntoView({ block: 'start', behavior: 'instant' });
      anchored.current = true;
    });
    return () => cancelAnimationFrame(frame);
  }, [initialAnchorId]);
}
