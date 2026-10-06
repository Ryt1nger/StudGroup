import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, renderApp, server, useScenario } from './helpers';

describe('important deadlines', () => {
  it('uses real buttons for filters and persistent task/deadline heading switch', async () => {
    useScenario();
    renderApp({ route: '/deadlines' });
    const active = await screen.findByRole('button', { name: 'Актуальные' });
    const archive = screen.getByRole('button', { name: 'Архив' });
    expect(active).toHaveAttribute('aria-pressed', 'true');
    expect(archive).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(archive);
    expect(archive).toHaveAttribute('aria-pressed', 'true');
    expect(active).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('link', { name: 'Архив' })).not.toBeInTheDocument();
    const tasks = screen.getByRole('button', { name: 'Задания' });
    const deadlines = screen.getByRole('button', { name: 'Дедлайны' });
    expect(deadlines).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(tasks);
    expect(await screen.findByRole('heading', { name: 'Задания', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Задания' })).toBe(tasks);
    expect(screen.getByRole('button', { name: 'Дедлайны' })).toBe(deadlines);
    await userEvent.click(deadlines);
    expect(await screen.findByRole('heading', { name: 'Дедлайны', level: 1 })).toBeInTheDocument();
    expect(tasks).toHaveAttribute('aria-pressed', 'false');
  });
  it('keeps a control point out of homework and preserves uncertain date windows', async () => {
    useScenario();
    const event = {
        id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', kind: 'control_point', subject: 'Основы российской государственности',
        title: 'КТ 1: эссе', description: '1000–1500 слов', deadline_at: null, date_only: false,
        window_start: '2026-10-10T00:00:00+03:00', window_end: '2026-10-16T00:00:00+03:00',
        date_hint: 'Период 10–15 октября. Срок сдачи уточняется.', needs_clarification: true, source_message_ids: [3636],
    };
    server.use(
      http.get(`${API}/deadlines`, () => HttpResponse.json({ generated_at: new Date().toISOString(), group_timezone: 'Europe/Moscow', items: [event] })),
      http.get(`${API}/deadlines/:id`, () => HttpResponse.json({ generated_at: new Date().toISOString(), group_timezone: 'Europe/Moscow', item: event })),
    );
    renderApp({ route: '/deadlines' });
    expect(await screen.findByText('КТ 1: эссе')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: event.subject, level: 3 })).toBeInTheDocument();
    expect(screen.getByText(/10 окт.*15 окт/)).toBeInTheDocument();
    expect(screen.queryByText(event.date_hint)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Открыть задание:/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: /Открыть событие:/ }));
    expect(await screen.findByRole('heading', { name: event.subject, level: 1 })).toBeInTheDocument();
    expect(await screen.findByText('1000–1500 слов')).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Что нужно сделать' })).toBeInTheDocument();
    expect(screen.queryByText('Выполнено мной')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(await screen.findByRole('button', { name: 'Актуальные' })).toBeInTheDocument();
  });
});
