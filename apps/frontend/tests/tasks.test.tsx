import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { buildWorld } from '../src/mocks/data';
import { decodeCursor, encodeCursor, selectHomework, type ListFilter } from '../src/mocks/homeworkList';
import { classifyTask, groupTaskSections, mergePages } from '../src/features/tasks/taskFilters';
import { renderApp, server, useScenario } from './helpers';

const TZ = 'Europe/Moscow';
// Monday 2026-10-05 09:00 Moscow.
const NOW = Date.parse('2026-10-05T06:00:00Z');
const HOUR = 3_600_000;

/** What the (mock) server returns for a filter, grouped by the client. */
function served(filter: ListFilter, now = NOW) {
  const world = buildWorld(now);
  const items = selectHomework([...world.summaries.values()], filter, now);
  return groupTaskSections(items, now, TZ).map((s) => ({ kind: s.kind, titles: s.items.map((i) => i.title) }));
}

describe('grouping (client) – the server filter is authoritative', () => {
  it('groups in the approved order and never re-filters what the server returned', () => {
    const world = buildWorld(NOW);
    const all = [...world.summaries.values()];
    // Hand the client a "today" response that contains a completed and a far-future card: both stay.
    const sections = groupTaskSections(all, NOW, TZ);
    expect(sections.map((s) => s.kind)).toEqual(['upcoming', 'unknown', 'done', 'overdue', 'cancelled']);
    const titles = sections.flatMap((s) => s.items.map((i) => i.title));
    expect(titles).toHaveLength(all.length);
    expect(sections.find((s) => s.kind === 'upcoming')!.items.map((i) => i.title)).toEqual(expect.arrayContaining(['ДЗ №12–140', 'КТ по истории']));
  });

  it('keeps cancelled cards in archive regardless of cancellation age and preserves details', () => {
    const world = buildWorld(NOW);
    const item = [...world.summaries.values()].find((card) => card.status === 'cancelled')!;
    const cutoff = Date.parse(item.cancelled_at!) + 12 * HOUR;
    item.updated_at = new Date(cutoff + HOUR).toISOString(); // later edits do not extend or reset it
    const ids = (at: number) => groupTaskSections([item], at, TZ).flatMap((s) => s.items.map((c) => c.id));
    expect(ids(cutoff - 1)).toContain(item.id);
    expect(ids(cutoff)).toContain(item.id);
    expect(world.details.has(item.id)).toBe(true);
    // A missing cancellation time is not guessed from updated_at.
    const unknown = { ...item, cancelled_at: null, updated_at: new Date(NOW - 100 * HOUR).toISOString() };
    expect(groupTaskSections([unknown], NOW, TZ)).toHaveLength(1);
  });

  it('classifies by group calendar day; date-only deadlines last the whole day', () => {
    const world = buildWorld(NOW);
    const item = { ...[...world.summaries.values()].find((i) => i.title === 'КТ по истории')! };
    const midday = Date.parse('2026-10-05T10:00:00Z');
    item.deadline = { state: 'known', at: '2026-10-05T15:00:00Z', date_only: false };
    expect(classifyTask(item, midday, 'Europe/Moscow')).toBe('due_today');
    expect(classifyTask(item, midday, 'Asia/Vladivostok')).toBe('upcoming');
    item.deadline = { state: 'known', at: '2026-10-05T00:00:00+03:00', date_only: true };
    expect(classifyTask(item, Date.parse('2026-10-05T20:30:00Z'), 'Europe/Moscow')).toBe('due_today');
  });

  it('merges pages by id: first position kept, newest data wins, no duplicates', () => {
    const world = buildWorld(NOW);
    const [a, b, c] = [...world.summaries.values()];
    const merged = mergePages([{ items: [a!, b!] }, { items: [{ ...a!, title: 'новее' }, c!] }]);
    expect(merged.map((i) => i.id)).toEqual([a!.id, b!.id, c!.id]);
    expect(merged[0]!.title).toBe('новее');
  });

  it('sections without items are never returned', () => {
    expect(groupTaskSections([], NOW, TZ)).toEqual([]);
    for (const f of ['all', 'today', 'week', 'mine'] as const) for (const s of served(f)) expect(s.titles.length).toBeGreaterThan(0);
  });
});

describe('mock server follows the contract', () => {
  it('«today»: active due today, no archived/completed/cancelled/unknown', () => {
    const flat = served('today').flatMap((s) => s.titles);
    expect(flat).toContain('ДЗ №12–140');
    expect(flat).not.toContain('Лабораторная работа №3');
    expect(flat).not.toEqual(expect.arrayContaining(['Реферат по социологии']));
    expect(flat).not.toContain('Эссе по маркетингу');
    expect(flat).not.toContain('Подготовить презентацию');
  });

  it('«week» is Monday–Sunday of the group; «mine» is completed excluding cancelled', () => {
    expect(served('week').flatMap((s) => s.titles)).not.toContain('Реферат по социологии'); // previous week
    expect(served('week').flatMap((s) => s.titles)).toEqual(expect.arrayContaining(['ДЗ №12–140', 'КТ по истории']));
    expect(served('mine').flatMap((s) => s.titles).sort()).toEqual(['Эссе по маркетингу']);
    expect(served('archive').flatMap((s) => s.titles)).toContain('Конспект по философии');
  });

  it('cancelled and past-deadline items appear only in archive, including after 12 hours', () => {
    const world = buildWorld(NOW);
    const all = [...world.summaries.values()];
    const cancelled = all.find((i) => i.status === 'cancelled')!;
    const cutoff = Date.parse(cancelled.cancelled_at!) + 12 * HOUR;
    expect(selectHomework(all, 'archive', cutoff - 1).map((i) => i.id)).toContain(cancelled.id);
    expect(selectHomework(all, 'all', cutoff).map((i) => i.id)).not.toContain(cancelled.id);
    expect(selectHomework(all, 'archive', cutoff).map((i) => i.id)).toContain(cancelled.id);
  });

  it('cursors are opaque, bound to the filter and carry the frozen selection time', () => {
    const token = encodeCursor({ f: 'week', o: 50, t: NOW });
    expect(decodeCursor(token)).toEqual({ f: 'week', o: 50, t: NOW });
    expect(decodeCursor('garbage')).toBeNull();
  });
});

function requests() {
  const urls: URL[] = [];
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith('/homework')) urls.push(url);
  });
  return urls;
}
const cards = () => screen.queryAllByRole('link', { name: /Открыть задание/ });

describe('Tasks screen', () => {
  beforeEach(() => {
    server.events.removeAllListeners();
    useScenario();
  });

  it('is available without mock mode and shows filters, headings in the approved order and cards', async () => {
    renderApp({ route: '/tasks' });
    expect(await screen.findByRole('heading', { name: 'Задания', level: 1 })).toBeInTheDocument();
    const group = screen.getByRole('group', { name: 'Фильтр заданий' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Все', 'Сегодня', 'На неделе', 'Выполнено мной', 'Архив']);
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Ближайшие', 'Срок уточняется', 'Выполнено']);
    expect(screen.queryByText('Реферат по социологии')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Архив' }));
    expect(await screen.findByText('Реферат по социологии')).toBeInTheDocument();
    expect(screen.getByText('Доклад по экологии')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Отменено' })).toBeInTheDocument();
    expect(within(screen.getByRole('link', { name: /Открыть задание: Доклад по экологии/ })).queryByText('Отменено')).not.toBeInTheDocument();
    expect(screen.queryByText('Срочно')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Выполнено мной' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Срок сегодня' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).not.toBeInTheDocument();
  });

  it('passes the selected filter to the server and restarts the list on every change', async () => {
    const urls = requests();
    renderApp({ route: '/tasks' });
    await screen.findByText('ДЗ №12–140');
    await userEvent.click(screen.getByRole('button', { name: 'Выполнено мной' }));
    expect(await screen.findByText('Эссе по маркетингу')).toBeInTheDocument();
    expect(screen.queryByText('Реферат по социологии')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Просрочено' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Сегодня' }));
    await waitFor(() => expect(screen.queryByText('Эссе по маркетингу')).not.toBeInTheDocument());
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
    expect(screen.queryByText('Лабораторная работа №3')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'На неделе' }));
    await screen.findByText('КТ по истории');
    expect(urls.map((u) => u.searchParams.get('filter'))).toEqual(['all', 'mine', 'today', 'week']);
    for (const u of urls) {
      expect(u.searchParams.get('limit')).toBe('50');
      expect(u.searchParams.get('cursor')).toBeNull();
    }
  });

  it('does not filter again on the client: whatever the server returns is shown', async () => {
    // «Выполнено мной» returns only completed cards; the older overdue one is simply not requested.
    renderApp({ route: '/tasks?filter=mine' });
    expect(await screen.findByText('Эссе по маркетингу')).toBeInTheDocument();
    expect(screen.queryByText('Конспект по философии')).not.toBeInTheDocument();
    expect(cards()).toHaveLength(1);
  });

  it.each([
    ['Все', 'Заданий пока нет'],
    ['Выполнено мной', 'Выполненных заданий пока нет'],
    ['Сегодня', 'На сегодня заданий нет'],
    ['На неделе', 'На этой неделе заданий нет'],
    ['Архив', 'Архив пока пуст'],
  ] as const)('empty state for «%s»', async (filter, title) => {
    useScenario('tasks-empty');
    renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('button', { name: filter }));
    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument();
    expect(cards()).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).not.toBeInTheDocument();
  });

  it('shows an error with retry when the list cannot be loaded', { timeout: 15000 }, async () => {
    useScenario('tasks-error');
    renderApp({ route: '/tasks' });
    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toBeInTheDocument();
    expect(cards()).toHaveLength(0);
    expect(screen.getByRole('button', { name: /Повторить|Попробовать/ })).toBeInTheDocument();
  });

  it('shows the no-access state for 403', async () => {
    useScenario('forbidden');
    renderApp({ route: '/tasks' });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(cards()).toHaveLength(0);
  });

  it('shows the offline state when the network fails', { timeout: 15000 }, async () => {
    useScenario('offline');
    renderApp({ route: '/tasks' });
    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toBeInTheDocument();
  });

  it('a card opens the existing detail screen', async () => {
    renderApp({ route: '/tasks?filter=archive' });
    await userEvent.click(await screen.findByRole('link', { name: /Открыть задание: Конспект по философии/ }));
    expect(await screen.findByRole('heading', { name: 'Конспект по философии' })).toBeInTheDocument();
  });
});

describe('Tasks pagination', () => {
  beforeEach(() => server.events.removeAllListeners());

  it('loads pages with «Загрузить ещё» (limit 50, cursor passed unchanged) until the end', async () => {
    useScenario('tasks-many');
    const urls = requests();
    renderApp({ route: '/tasks' });
    const more = await screen.findByRole('button', { name: 'Загрузить ещё' });
    expect(cards()).toHaveLength(50);
    await userEvent.click(more);
    await waitFor(() => expect(cards()).toHaveLength(100));
    await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }));
    await waitFor(() => expect(cards()).toHaveLength(124));
    expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).not.toBeInTheDocument();
    expect(urls.map((u) => (u.searchParams.get('cursor') ? 'cursor' : 'first'))).toEqual(['first', 'cursor', 'cursor']);
    expect(urls.every((u) => u.searchParams.get('limit') === '50' && u.searchParams.get('filter') === 'all')).toBe(true);
  });

  it('merges duplicated ids from the next page without duplicate cards', async () => {
    useScenario('tasks-dup-page');
    renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }));
    await waitFor(() => expect(cards()).toHaveLength(100));
    const names = cards().map((c) => c.getAttribute('aria-label'));
    expect(new Set(names).size).toBe(names.length);
  });

  it('an empty first page with a cursor is not an empty list', async () => {
    useScenario('tasks-empty-first-page');
    renderApp({ route: '/tasks' });
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Заданий пока нет' })).not.toBeInTheDocument();
  });

  it('a failed «Загрузить ещё» keeps the loaded cards, offers a retry and then continues', { timeout: 15000 }, async () => {
    useScenario('tasks-more-fails');
    renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }));
    expect(await screen.findByText(/Не удалось загрузить следующие задания/, {}, { timeout: 8000 })).toBeInTheDocument();
    expect(cards()).toHaveLength(50);
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(cards()).toHaveLength(100));
    expect(screen.queryByText(/Не удалось загрузить следующие задания/)).not.toBeInTheDocument();
  });

  it('an expired cursor (422 invalid_request) restarts the list once, without looping', async () => {
    useScenario('tasks-cursor-expired');
    const urls = requests();
    renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }));
    await waitFor(() => expect(urls.filter((u) => !u.searchParams.get('cursor'))).toHaveLength(2));
    await waitFor(() => expect(cards()).toHaveLength(50));
    await new Promise((r) => setTimeout(r, 300));
    // first page, the rejected cursor, the single restart: nothing more.
    expect(urls).toHaveLength(3);
    expect(screen.queryByText(/Не удалось загрузить следующие задания/)).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }));
    await waitFor(() => expect(cards()).toHaveLength(100));
  });
});

describe('completion sync', () => {
  beforeEach(() => {
    server.events.removeAllListeners();
    useScenario();
  });

  it('marking and unmarking on the detail screen restarts the list and updates Tasks and Today', async () => {
    const urls = requests();
    const { router } = renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('link', { name: /Открыть задание: КТ по истории/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Выполнено мной' }));
    await screen.findByRole('button', { name: /Снять отметку/ });

    await router.navigate('/tasks?filter=mine');
    const card = await screen.findByRole('link', { name: /Открыть задание: КТ по истории/ });
    expect(within(card).getByText('Выполнено мной')).toBeInTheDocument();

    await router.navigate('/tasks');
    await screen.findByText('ДЗ №12–140'); // the "all" list, not the previous "mine" view
    const done = screen.getByRole('region', { name: 'Выполнено' });
    expect(within(done).getByText('КТ по истории')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Прошедшие' })).not.toBeInTheDocument();

    await userEvent.click(within(done).getByRole('link', { name: /Открыть задание: КТ по истории/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Снять отметку/ }));
    await screen.findByRole('button', { name: 'Выполнено мной' });
    await router.navigate('/tasks?filter=mine');
    await waitFor(() => expect(screen.queryByText('КТ по истории')).not.toBeInTheDocument());
    await router.navigate('/tasks');
    await screen.findByText('ДЗ №12–140');
    expect(within(screen.getByRole('region', { name: 'Ближайшие' })).getByText('КТ по истории')).toBeInTheDocument();
    // Every list request after a mutation starts again from the first page.
    expect(urls.every((u) => !u.searchParams.get('cursor'))).toBe(true);
  });

  it('completing a Today card keeps its active deadline visible with a completion mark', async () => {
    const { router } = renderApp({ route: '/today' });
    await userEvent.click(await screen.findByRole('link', { name: /Открыть задание: ДЗ №12–140/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Выполнено мной' }));
    await screen.findByRole('button', { name: /Снять отметку/ });
    await router.navigate('/today');
    const card = await screen.findByRole('link', { name: /Открыть задание: ДЗ №12–140/ });
    await waitFor(() => expect(within(card).getByText('Выполнено мной')).toBeInTheDocument());
    await router.navigate('/tasks?filter=mine');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Задания', level: 1 })).toBeInTheDocument();
      expect(screen.getByText('ДЗ №12–140')).toBeInTheDocument();
    });
  });

  it('a revision conflict also restarts the list', async () => {
    useScenario('completion-conflict');
    const urls = requests();
    const { router } = renderApp({ route: '/tasks' });
    await screen.findByText('ДЗ №12–140');
    await userEvent.click(screen.getByRole('link', { name: /Открыть задание: ДЗ №12–140/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Выполнено мной' }));
    await screen.findByRole('alert');
    await router.navigate('/tasks');
    await screen.findByText('ДЗ №12–140');
    await waitFor(() => expect(urls.length).toBeGreaterThanOrEqual(2));
    expect(urls.every((u) => !u.searchParams.get('cursor'))).toBe(true);
  });
});
