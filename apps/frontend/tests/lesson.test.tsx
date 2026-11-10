import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderApp, useScenario } from './helpers';

describe('lesson page', () => {
  it('opens from the schedule and shows every contract field, then returns', async () => {
    useScenario();
    renderApp({ route: '/schedule' });
    const row = (await screen.findAllByRole('link', { name: /Математический анализ/ }))[0]!;
    await userEvent.click(row);
    expect(await screen.findByRole('heading', { name: 'О паре' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Математический анализ', level: 1 })).toBeInTheDocument();
    const card = screen.getByRole('heading', { name: 'О паре' }).parentElement!;
    for (const label of ['Дата', 'Время', 'Длительность', 'Преподаватель', 'Аудитория', 'Статус'])
      expect(within(card).getByText(label)).toBeInTheDocument();
    expect(within(card).getByText('Иванов А. С.')).toBeInTheDocument();
    expect(within(card).getByText('Ауд. 320')).toBeInTheDocument();
    expect(within(card).getByText('По расписанию')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'Назад' }));
    expect(await screen.findByRole('heading', { name: 'Расписание', level: 1 })).toBeInTheDocument();
  });

  it('shows an equally tall round copy button next to the join link and copies the meeting URL', async () => {
    useScenario();
    const url = 'https://meet.example.edu/join?id=12345';
    const writeText = vi.fn().mockResolvedValue(undefined);
    const oldClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    try {
      const { router } = renderApp({ route: '/today' });
      await act(async () => {
        await router.navigate('/schedule/lesson/copy-test', {
          state: {
            lesson: {
              id: 'copy-test',
              subject: 'Английский язык',
              starts_at: '2026-10-05T07:00:00Z',
              ends_at: '2026-10-05T08:30:00Z',
              teacher: null,
              location: null,
              status: 'scheduled',
              online_url: url,
            },
            timeZone: 'Europe/Moscow',
          },
        });
      });

      const join = await screen.findByRole('link', { name: /Подключиться к паре/ });
      const copy = screen.getByRole('button', { name: 'Скопировать ссылку на пару' });
      expect(join).toHaveAttribute('href', url);
      expect(join).toHaveClass('lesson-join-main');
      expect(copy).toHaveClass('lesson-join-copy');
      expect(copy.parentElement).toBe(join.parentElement);
      expect(copy.querySelector('svg')).toBeInTheDocument();

      await userEvent.click(copy);
      expect(writeText).toHaveBeenCalledExactlyOnceWith(url);
      expect(await screen.findByRole('status')).toHaveTextContent('Ссылка скопирована');
      expect(router.state.location.pathname).toBe('/schedule/lesson/copy-test');
    } finally {
      if (oldClipboard) Object.defineProperty(navigator, 'clipboard', oldClipboard);
      else Reflect.deleteProperty(navigator, 'clipboard');
    }
  });

  it('shows a not-found state for an unknown lesson id', async () => {
    useScenario();
    renderApp({ route: '/schedule/lesson/unknown' });
    expect(await screen.findByRole('link', { name: 'К расписанию' })).toBeInTheDocument();
    expect(screen.getAllByText('Пара не найдена').length).toBeGreaterThan(0);
  });
});
