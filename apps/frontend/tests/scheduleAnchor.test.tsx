import { render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { LessonOccurrence, ScheduleResponse } from '@studgroup/shared-types';
import { scheduleAnchor, dayAnchorId, lessonAnchorId, useScheduleEntryAnchor } from '../src/features/schedule/scheduleAnchor';
import { mapSchedule } from '../src/features/schedule/mapSchedule';
import { renderApp, useScenario } from './helpers';

const now = Date.parse('2026-10-08T11:15:00+03:00');
const lesson = (id: string, start: string, end: string, cancelled = false): LessonOccurrence => ({ id, subject: 'Математика', starts_at: `2026-10-08T${start}:00+03:00`, ends_at: `2026-10-08T${end}:00+03:00`, status: cancelled ? 'cancelled' : 'scheduled', location: null, teacher: null });
const schedule = (lessons: LessonOccurrence[]): ScheduleResponse => ({ generated_at: new Date(now).toISOString(), group_timezone: 'Europe/Moscow', start: '2026-10-05', end: '2026-10-11', week_state: 'ready', selected_week: null, first_week_anchor: null, lessons });

function AnchorFixture({ anchor }: { anchor?: string }) {
  useScheduleEntryAnchor(anchor);
  return <div id={anchor} />;
}

describe('schedule entry anchor', () => {
  it('prefers the running lesson over a later lesson', () => {
    expect(scheduleAnchor(schedule([lesson('past', '09:00', '10:20'), lesson('running', '10:40', '12:00'), lesson('next', '12:20', '13:40')]), now)).toBe(lessonAnchorId('running'));
  });
  it('between lessons selects the next non-cancelled lesson today', () => {
    expect(scheduleAnchor(schedule([lesson('cancelled', '11:20', '12:00', true), lesson('next', '12:20', '13:40')]), now)).toBe(lessonAnchorId('next'));
  });
  it('after classes or on a free day anchors the day, including a day absent from lessons', () => {
    const data = schedule([lesson('past', '09:00', '10:20')]);
    expect(scheduleAnchor(data, now)).toBe(dayAnchorId('2026-10-08'));
    expect(scheduleAnchor(schedule([]), now)).toBe(dayAnchorId('2026-10-08'));
    expect(mapSchedule(schedule([]), now).days[0]?.date).toBe('2026-10-08');
  });
  it('uses the group timezone rather than UTC calendar day', () => {
    expect(scheduleAnchor(schedule([]), Date.parse('2026-10-07T22:30:00Z'))).toBe(dayAnchorId('2026-10-08'));
  });
  it('scrolls after loading but does not scroll again on a background refresh', async () => {
    const scroll = vi.mocked(Element.prototype.scrollIntoView);
    scroll.mockClear();
    const { rerender } = render(<AnchorFixture />);
    const data = schedule([]);
    rerender(<AnchorFixture anchor={scheduleAnchor(data, now)} />);
    await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
    expect(scroll.mock.instances[0]).toHaveProperty('id', dayAnchorId('2026-10-08'));
    rerender(<AnchorFixture anchor={scheduleAnchor(data, now + 60_000)} />);
    expect(scroll).toHaveBeenCalledOnce();
  });
  it('production schedule entry scrolls to a rendered anchor after the shell resets scroll', async () => {
    useScenario();
    const scroll = vi.mocked(Element.prototype.scrollIntoView);
    scroll.mockClear();
    renderApp({ route: '/schedule' });
    await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
    expect((scroll.mock.instances[0] as HTMLElement).id).toMatch(/^schedule-(day|lesson)-/);
  });
});
