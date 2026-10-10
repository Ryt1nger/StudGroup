import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { renderApp, useScenario } from './helpers';

describe('lesson page', () => {
  it('opens from the schedule and shows every contract field, then returns', async () => {
    useScenario();
    renderApp({ route: '/schedule' });
    const row = (await screen.findAllByText('Ауд. 320 · Иванов А. С.'))[0]!.closest('button');
    expect(row).not.toBeNull();
    await userEvent.click(row!);
    expect(await screen.findByRole('heading', { name: 'О паре' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Математический анализ', level: 1 })).toBeInTheDocument();
    const card = screen.getByRole('region', { name: 'О паре' });
    for (const label of ['Дата', 'Время', 'Длительность', 'Преподаватель', 'Аудитория', 'Статус'])
      expect(within(card).getByText(label)).toBeInTheDocument();
    expect(within(card).getByText('Иванов А. С.')).toBeInTheDocument();
    expect(within(card).getByText('Ауд. 320')).toBeInTheDocument();
    expect(within(card).getByText('По расписанию')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(await screen.findByRole('heading', { name: 'Расписание', level: 1 })).toBeInTheDocument();
  });

  it('shows a not-found state for an unknown lesson id', async () => {
    useScenario();
    renderApp({ route: '/schedule/lesson/unknown' });
    expect(await screen.findByRole('button', { name: 'К расписанию' })).toBeInTheDocument();
    expect(screen.getAllByText('Пара не найдена').length).toBeGreaterThan(0);
  });
});
