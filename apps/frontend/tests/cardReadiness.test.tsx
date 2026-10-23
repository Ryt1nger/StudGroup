import type { HomeworkDetail } from '@studgroup/shared-types';
import { contractExamples } from '@studgroup/shared-types/examples';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { toHomeworkCardViewModel } from '../src/features/homework/cardViewModel';
import { API, renderApp, server, useScenario } from './helpers';

const HW = '44444444-4444-4444-8444-444444444444';
const base = contractExamples.HomeworkDetailAvailable as unknown as HomeworkDetail;

describe('published learning card readiness', () => {
  it('keeps the current contract fully compatible', () => {
    const view = toHomeworkCardViewModel(base);
    expect(view.kind).toBe('homework');
    expect(view.deadlineCertainty).toBe('from_source');
    expect(view.sources).toEqual([base.source_detail]);
    expect(view.updates).toEqual([]);
  });

  it('adapts the proposed extension without exposing wire codes', async () => {
    useScenario();
    const extended = {
      ...base,
      // The updates block is for editors only.
      permissions: [...base.permissions, 'homework.edit'],
      card_kind: 'test',
      deadline: { ...base.deadline, certainty: 'needs_clarification' },
      materials: [
        { id: 'photo-1', title: 'Условие, страница 1', url: 'https://example.com/page-1.jpg', kind: 'photo' },
        { id: 'doc-1', title: 'Методичка.pdf', url: 'https://example.com/guide.pdf', kind: 'document' },
      ],
      sources: [
        base.source_detail,
        {
          kind: 'telegram_group_message',
          imported: false,
          availability: { state: 'unavailable', reason: 'no_access' },
          message_date: '2026-10-13T08:30:00Z',
          excerpt: null,
          action: { type: 'none', url: null },
        },
      ],
      updates: [
        {
          id: 'u1',
          kind: 'supplement',
          text: 'Добавлены страницы 12–14.',
          created_at: '2026-10-13T09:00:00Z',
        },
        { id: 'u2', kind: 'change', text: 'Заменён файл с условием.', created_at: '2026-10-13T10:00:00Z' },
      ],
    };
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(extended)));

    const { adapter } = renderApp({ route: `/homework/${HW}` });
    expect(await screen.findByText('Тест')).toBeInTheDocument();
    // this viewer edits cards: one small mark, no caption about where the date came from
    expect(screen.getByText('Требует уточнения')).toBeInTheDocument();
    expect(screen.queryByText('Дата требует уточнения')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Дополнения и изменения' })).toBeInTheDocument();
    expect(screen.getByText('Добавлены страницы 12–14.')).toBeInTheDocument();

    const materials = screen.getByRole('heading', { name: 'Материалы' }).parentElement!;
    expect(within(materials).getByText('Фотография')).toBeInTheDocument();
    expect(within(materials).getByText('Документ')).toBeInTheDocument();

    expect(screen.getByRole('heading', { name: /^Источники/ })).toBeInTheDocument();
    // collapsed by default: the unavailable-source explanation is hidden until the arrow is used
    expect(screen.getByText('У вас нет доступа к исходному сообщению.')).not.toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /^Источники:/ }));
    expect(screen.getByText('У вас нет доступа к исходному сообщению.')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /Сообщение учебной группы/ }));
    expect(adapter?.openTelegramLink).toHaveBeenCalledWith('https://t.me/c/1234567890/42');
    expect(screen.queryByText('needs_clarification')).not.toBeInTheDocument();
  });

  it('shows an honest absence of a deadline without inventing a date', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () =>
        HttpResponse.json({ ...base, deadline: { state: 'unknown', at: null, date_only: false } }),
      ),
    );
    renderApp({ route: `/homework/${HW}` });
    await screen.findByRole('heading', { level: 1 });
    // no banner and no caption for a student; the headman gets one small mark instead
    expect(screen.queryByText(/Мы уточним у старосты/)).not.toBeInTheDocument();
    expect(screen.queryByText('Срок не указан')).not.toBeInTheDocument();
    expect(screen.queryByText('Дата определена автоматически')).not.toBeInTheDocument();
    expect(screen.queryByText('Требует уточнения')).not.toBeInTheDocument();
  });

  it('shows the headman only a small "needs clarification" mark for a card without a deadline', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () =>
        HttpResponse.json({
          ...base,
          permissions: [...base.permissions, 'homework.edit'],
          deadline: { state: 'unknown', at: null, date_only: false },
        }),
      ),
    );
    renderApp({ route: `/homework/${HW}` });
    expect(await screen.findByText('Требует уточнения')).toBeInTheDocument();
    expect(screen.queryByText(/Мы уточним у старосты/)).not.toBeInTheDocument();
    expect(screen.queryByText('Срок не указан')).not.toBeInTheDocument();
  });
});
