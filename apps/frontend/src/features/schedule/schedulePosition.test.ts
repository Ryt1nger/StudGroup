import { describe, expect, it } from 'vitest';
import type { LessonOccurrence } from '@studgroup/shared-types';
import { lessonAt, positionSchedule, scheduleExpansions, scheduleY } from './schedulePosition';

const occurrence = (starts_at: string, ends_at: string) =>
  ({ starts_at, ends_at }) as LessonOccurrence;

function rect(top: number): DOMRect {
  return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: top, toJSON: () => ({}) };
}

describe('schedule positioning', () => {
  it('makes room for a two-line title and teacher without shifting preceding hours', () => {
    const expanded = scheduleExpansions([
      { startMinute: 260, endMinute: 340, height: 116 }, // 12:20–13:40, screenshot
      { startMinute: 360, endMinute: 440, height: 70 }, // 14:00–15:20 fits its slot
    ], 64);
    expect(expanded).toEqual([{ endMinute: 340, extra: 116 + 4 - (80 * 64) / 60 }]);
    expect(scheduleY(260, 64, expanded)).toBeCloseTo((260 * 64) / 60);
    expect(scheduleY(360, 64, expanded) - scheduleY(260, 64, expanded)).toBeCloseTo(
      (100 * 64) / 60 + expanded[0]!.extra,
    );
  });

  it('reserves vertical space for very short lessons and never compresses later slots', () => {
    const expanded = scheduleExpansions([
      { startMinute: 120, endMinute: 140, height: 85 },
      { startMinute: 150, endMinute: 210, height: 90 },
    ], 64);
    const firstEnd = scheduleY(140, 64, expanded);
    const firstStart = scheduleY(120, 64, expanded);
    expect(firstEnd - firstStart).toBeGreaterThanOrEqual(89);
    expect(scheduleY(150, 64, expanded)).toBeGreaterThanOrEqual(firstStart + 85 + 4);
    expect(scheduleY(240, 64, expanded)).toBeGreaterThan((240 * 64) / 60);
  });

  it('detects when the live line crosses a lesson card', () => {
    const lessons = [occurrence('2026-10-24T09:00:00Z', '2026-10-24T10:30:00Z')];
    expect(lessonAt(lessons, Date.parse('2026-10-24T09:45:00Z'))).toBe(true);
    expect(lessonAt(lessons, Date.parse('2026-10-24T10:30:00Z'))).toBe(false);
  });

  it('anchors to the lesson in progress before the current-time line', () => {
    const scroll = document.createElement('div');
    const pinned = document.createElement('div');
    const line = document.createElement('div');
    const current = document.createElement('div');
    pinned.className = 'pinned';
    line.className = 'nowline';
    current.className = 'ev';
    current.dataset.start = '100';
    current.dataset.end = '300';
    Object.defineProperty(pinned, 'offsetHeight', { value: 80 });
    scroll.getBoundingClientRect = () => rect(20);
    line.getBoundingClientRect = () => rect(600);
    current.getBoundingClientRect = () => rect(400);
    scroll.append(pinned, line, current);

    positionSchedule(scroll, 200);

    expect(scroll.scrollTop).toBe(280);
  });

  it('anchors to the live line when no lesson is in progress', () => {
    const scroll = document.createElement('div');
    const line = document.createElement('div');
    line.className = 'nowline';
    scroll.getBoundingClientRect = () => rect(20);
    line.getBoundingClientRect = () => rect(600);
    scroll.append(line);

    positionSchedule(scroll, 200);

    expect(scroll.scrollTop).toBe(440);
  });
});
