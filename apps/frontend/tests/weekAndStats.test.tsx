import { fireEvent, render, screen } from '@testing-library/react';
import type { HomeworkSummary } from '@studgroup/shared-types';
import { describe, expect, it, vi } from 'vitest';
import { weekKeys, WeekStrip } from '../src/features/shared/WeekStrip';
import { homeworkStats } from '../src/features/today/homeworkStats';
import { exampleSummary } from '../src/mocks/data';

describe('fixed calendar week', () => {
  it('starts on Monday across month and year boundaries', () => {
    expect(weekKeys('2027-01-01')).toEqual(['2026-12-28', '2026-12-29', '2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02', '2027-01-03']);
  });

  it('animates both directions when the visible week changes, but not on day selection', () => {
    const props = {
      marked: new Set<string>(),
      onSelect: vi.fn(),
      onWeekChange: vi.fn(),
    };
    const first = weekKeys('2027-01-01');
    const next = weekKeys('2027-01-08');
    const previous = weekKeys('2026-12-25');
    const { container, rerender } = render(<WeekStrip {...props} days={first} selected={4} />);
    const track = () => container.querySelector('.week-track');
    expect(track()).toHaveAttribute('data-direction', '0');

    rerender(<WeekStrip {...props} days={first} selected={5} />);
    expect(track()).toHaveAttribute('data-direction', '0');

    rerender(<WeekStrip {...props} days={next} selected={0} />);
    expect(track()).toHaveAttribute('data-direction', '1');
    expect(screen.getByRole('button', { name: 'Пн, 4' })).toHaveAttribute('aria-pressed', 'true');

    rerender(<WeekStrip {...props} days={previous} selected={0} />);
    expect(track()).toHaveAttribute('data-direction', '-1');
    expect(screen.getByRole('button', { name: 'Пн, 21' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('swipes complete weeks and keeps vertical scrolling separate', () => {
    const onWeekChange = vi.fn();
    render(<WeekStrip days={weekKeys('2027-01-01')} selected={4} marked={new Set()} onSelect={vi.fn()} onWeekChange={onWeekChange} />);
    const strip = screen.getByRole('group', { name: 'День' });
    expect(screen.getByRole('button', { name: 'Пт, 1' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.touchStart(strip, { touches: [{ clientX: 200, clientY: 100 }] });
    fireEvent.touchEnd(strip, { changedTouches: [{ clientX: 70, clientY: 105 }] });
    expect(onWeekChange).toHaveBeenCalledWith(1);
    fireEvent.touchStart(strip, { touches: [{ clientX: 200, clientY: 100 }] });
    fireEvent.touchEnd(strip, { changedTouches: [{ clientX: 170, clientY: 180 }] });
    expect(onWeekChange).toHaveBeenCalledTimes(1);
  });
});

describe('homework counters', () => {
  const task = (id: string, completed = false, urgency: HomeworkSummary['urgency'] = 'normal'): HomeworkSummary => ({
    ...exampleSummary, id, urgency,
    my_state: { ...exampleSummary.my_state, completion: completed ? 'completed' : 'pending' },
  });

  it('keeps total stable when a personal mark changes or is undone', () => {
    const first = [task('a'), task('b', false, 'urgent')];
    expect(homeworkStats(first)).toEqual({ total: 2, done: 0, ahead: 2, urgent: 1 });
    expect(homeworkStats([task('a', true), first[1]!])).toEqual({ total: 2, done: 1, ahead: 1, urgent: 1 });
    expect(homeworkStats(first)).toEqual({ total: 2, done: 0, ahead: 2, urgent: 1 });
  });

  it('deduplicates reloaded pages and counts only active cards', () => {
    const cancelled = { ...task('c'), status: 'cancelled' as const };
    expect(homeworkStats([task('a'), task('a', true), task('b', false, 'super_urgent'), cancelled])).toEqual({ total: 2, done: 1, ahead: 1, urgent: 1 });
    expect(homeworkStats([])).toEqual({ total: 0, done: 0, ahead: 0, urgent: 0 });
  });
});
