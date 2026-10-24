import { describe, expect, it } from 'vitest';
import type { LessonOccurrence } from '@studgroup/shared-types';
import { lessonAt, positionSchedule } from './schedulePosition';

const occurrence = (starts_at: string, ends_at: string) =>
  ({ starts_at, ends_at }) as LessonOccurrence;

function rect(top: number): DOMRect {
  return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: top, toJSON: () => ({}) };
}

describe('schedule positioning', () => {
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
