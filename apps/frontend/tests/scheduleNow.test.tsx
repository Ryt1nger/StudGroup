import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { LessonOccurrence, ScheduleResponse } from '@studgroup/shared-types';
import { mapSchedule } from '../src/features/schedule/mapSchedule';
import { ScheduleTimeline } from '../src/features/schedule/ScheduleTimeline';
import { TelegramProvider } from '../src/telegram/TelegramProvider';
import { makeAdapter } from './helpers';

const url = 'https://my.mts-link.ru/j/Ranepa/12345';
const NOW = Date.parse('2026-10-20T10:50:00+03:00');

function lesson(over: Partial<LessonOccurrence>): LessonOccurrence {
  return {
    id: 'l1',
    subject: 'Математический анализ',
    teacher: null,
    location: 'Ауд. 320',
    starts_at: '2026-10-20T10:40:00+03:00',
    ends_at: '2026-10-20T12:10:00+03:00',
    status: 'scheduled',
    online_url: null,
    ...over,
  };
}

function day(lessons: LessonOccurrence[]) {
  const data: ScheduleResponse = {
    generated_at: '2026-10-20T07:50:00Z',
    group_timezone: 'Europe/Moscow',
    start: '2026-10-19',
    end: '2026-10-25',
    selected_week: null,
    first_week_anchor: null,
    week_state: 'ready',
    lessons,
  };
  return mapSchedule(data, NOW, () => undefined).days.find((d) => d.items.length > 0)!;
}

function show(lessons: LessonOccurrence[], adapter = makeAdapter()) {
  render(
    <TelegramProvider adapter={adapter}>
      <ScheduleTimeline day={day(lessons)} />
    </TelegramProvider>,
  );
  return adapter;
}

describe('«Сейчас» on the schedule', () => {
  it('is not shown for an in-person lesson in progress', () => {
    show([lesson({})]);
    expect(screen.queryByText('Сейчас')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    // the card is still one button that opens the lesson
    expect(screen.getByRole('button', { name: /Математический анализ/ })).toBeInTheDocument();
  });

  it('is a link to the lesson for an online lesson in progress', () => {
    const adapter = show([lesson({ online_url: url })]);
    const link = screen.getByRole('link', { name: 'Сейчас' });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    fireEvent.click(link);
    expect(adapter.openExternalLink).toHaveBeenCalledWith(url);
    // the separate join button would repeat it
    expect(screen.queryByRole('link', { name: 'Подключиться к занятию' })).not.toBeInTheDocument();
  });

  it('keeps the card openable next to the link, without nesting one control in another', () => {
    const open = vi.fn();
    const data = day([lesson({ online_url: url })]);
    data.items[0]!.onOpen = open;
    render(
      <TelegramProvider adapter={makeAdapter()}>
        <ScheduleTimeline day={data} />
      </TelegramProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Математический анализ' }));
    expect(open).toHaveBeenCalledTimes(1);
    const link = screen.getByRole('link', { name: 'Сейчас' });
    expect(link.closest('button')).toBeNull();
    fireEvent.click(link);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('is not shown for an unsafe (non-https) online link', () => {
    show([lesson({ online_url: 'http://zoom.us/j/1' })]);
    expect(screen.queryByText('Сейчас')).not.toBeInTheDocument();
  });

  it('upcoming online lesson: no «Сейчас», but the join button', () => {
    show([
      lesson({
        starts_at: '2026-10-20T15:40:00+03:00',
        ends_at: '2026-10-20T17:10:00+03:00',
        online_url: url,
      }),
    ]);
    expect(screen.queryByText('Сейчас')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Подключиться к занятию' })).toHaveAttribute('href', url);
  });

  it('is not shown for a cancelled online lesson', () => {
    show([lesson({ online_url: url, status: 'cancelled' })]);
    expect(screen.queryByText('Сейчас')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
