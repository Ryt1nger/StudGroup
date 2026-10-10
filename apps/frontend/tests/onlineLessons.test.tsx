import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { JoinLessonLink } from '../src/ui/JoinLessonLink';
import { mapSchedule } from '../src/features/schedule/mapSchedule';
import { TelegramProvider } from '../src/telegram/TelegramProvider';
import { makeAdapter } from './helpers';

const url = 'https://my.mts-link.ru/j/Ranepa/12345';

describe('online lesson joining', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('renders a real protected external link', () => {
    render(
      <TelegramProvider adapter={makeAdapter()}>
        <JoinLessonLink url={url} />
      </TelegramProvider>,
    );
    const link = screen.getByRole('link', { name: 'Подключиться к занятию' });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
  it('opens through Telegram only after a user click', () => {
    const openExternalLink = vi.fn();
    const adapter = makeAdapter({ openExternalLink });
    render(
      <TelegramProvider adapter={adapter}>
        <JoinLessonLink url={url} />
      </TelegramProvider>,
    );
    expect(openExternalLink).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('link'));
    expect(openExternalLink).toHaveBeenCalledWith(url);
  });
  it('does not show unsafe URLs', () => {
    render(
      <TelegramProvider adapter={makeAdapter()}>
        <JoinLessonLink url="javascript:alert(1)" />
        <JoinLessonLink url="https://user:pass@zoom.us/j/123" />
      </TelegramProvider>,
    );
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
  it('only shows joining action for upcoming/current uncancelled lessons', () => {
    const data = {
      generated_at: '2026-10-09T08:00:00Z',
      group_timezone: 'Europe/Moscow',
      start: '2026-10-05',
      end: '2026-10-11',
      selected_week: null,
      first_week_anchor: null,
      week_state: 'ready' as const,
      lessons: [
        {
          id: 'lesson',
          subject: 'Матанализ',
          teacher: null,
          location: 'СДО',
          starts_at: '2026-10-09T10:40:00+03:00',
          ends_at: '2026-10-09T12:00:00+03:00',
          status: 'scheduled' as const,
          online_url: url,
        },
      ],
    };
    const before = mapSchedule(data, Date.parse('2026-10-09T11:00:00+03:00'));
    expect(before.days.flatMap((d) => d.items)[0]?.onlineUrl).toBe(url);
    const after = mapSchedule(data, Date.parse('2026-10-09T12:00:00+03:00'));
    expect(after.days.flatMap((d) => d.items)[0]?.onlineUrl).toBeNull();
    const cancelled = mapSchedule(
      { ...data, lessons: [{ ...data.lessons[0]!, status: 'cancelled' }] },
      Date.parse('2026-10-09T11:00:00+03:00'),
    );
    expect(cancelled.days.flatMap((d) => d.items)[0]?.onlineUrl).toBeNull();
  });
});
