import type { EventReview, PublishedEvent } from '@studgroup/shared-types';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { eventDays, eventDate, groupEventOnDay } from '../src/features/events/calendar';
import { API, renderApp, server, useScenario } from './helpers';

const ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

async function go(router: ReturnType<typeof renderApp>['router'], route: string) {
  await act(async () => {
    await router.navigate(route);
  });
}

describe('separate persisted group events', () => {
  it('hides navigation and makes no event requests when capabilities are absent', async () => {
    useScenario();
    renderApp({ route: '/events' });
    expect(await screen.findByText('События недоступны')).toBeInTheDocument();
    expect(
      within(screen.getByRole('navigation')).queryByRole('link', { name: 'События' }),
    ).not.toBeInTheDocument();
  });

  it('publishes a draft, shares it with the calendar, moves and cancels the same entity', async () => {
    useScenario('events');
    const app = renderApp({ route: '/events' });
    expect(await screen.findByText('Пока нет событий')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'Проверить черновики и изменения' }));
    await userEvent.click(await screen.findByRole('link', { name: /Открытая лекция о космосе/ }));
    expect(await screen.findByLabelText('Название')).toHaveValue('Открытая лекция о космосе');
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить публикацию' }));
    expect(
      await screen.findByRole('heading', { name: 'Открытая лекция о космосе', level: 1 }),
    ).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe(`/events/${ID}`);
    await userEvent.click(screen.getByRole('link', { name: 'Источник в Telegram' }));
    expect(app.adapter?.openTelegramLink).toHaveBeenCalledWith('https://t.me/c/123456/31');
    await go(app.router, '/today?day=1');
    expect(await screen.findByRole('heading', { name: 'Открытая лекция о космосе' })).toBeInTheDocument();
    await go(app.router, `/events/review/${ID}`);
    const date = await screen.findByLabelText('Дата начала');
    await userEvent.clear(date);
    await userEvent.type(date, '2026-10-08');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));
    expect(await screen.findByText('Перенесено')).toBeInTheDocument();
    await go(app.router, '/today?day=1');
    await waitFor(() => expect(app.queryClient.isFetching({ queryKey: ['group-events'] })).toBe(0));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Открытая лекция о космосе' })).not.toBeInTheDocument(),
    );
    await go(app.router, '/today?day=3');
    expect(await screen.findByRole('heading', { name: 'Открытая лекция о космосе' })).toBeInTheDocument();
    await go(app.router, `/events/review/${ID}`);
    await userEvent.click(await screen.findByRole('button', { name: 'Отменить событие' }));
    await userEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Отменить событие' }),
    );
    expect(await screen.findByText('Мероприятие отменено')).toBeInTheDocument();
    await go(app.router, '/today?day=3');
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Открытая лекция о космосе' })).not.toBeInTheDocument(),
    );
    await go(app.router, '/events?history=1');
    expect(await screen.findByRole('heading', { name: 'Открытая лекция о космосе' })).toBeInTheDocument();
  });

  it('shows explicit loading, empty and permission error states', async () => {
    useScenario('events');
    let finish: (() => void) | undefined;
    server.use(
      http.get(`${API}/group-events`, async () => {
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
        return HttpResponse.json({ events: [] });
      }),
    );
    const app = renderApp({ route: '/events' });
    expect(await screen.findByRole('status', { name: 'Загрузка событий' })).toBeInTheDocument();
    await waitFor(() => expect(finish).toBeTypeOf('function'));
    await act(async () => {
      finish!();
    });
    expect(await screen.findByText('Пока нет событий')).toBeInTheDocument();
    app.unmount();
    useScenario('events');
    server.use(http.get(`${API}/group-events`, () => HttpResponse.json({}, { status: 403 })));
    renderApp({ route: '/events' });
    expect(await screen.findByText('Нет доступа')).toBeInTheDocument();
  });

  it('keeps editor input and the original revision after a conflict until explicit reload', async () => {
    useScenario('events');
    let revision = 1;
    const revisions: number[] = [];
    const initial: EventReview = {
      id: ID,
      revision: 1,
      state: 'draft',
      title: 'Лекция',
      description: '',
      starts_on: '2026-10-08',
      audience: 'group',
      source_availability: 'available',
      source_url: 'https://t.me/c/123456/31',
      manual_lock: false,
      findings: [],
      retained_until: '2026-10-16T00:00:00+03:00',
    };
    server.use(
      http.get(`${API}/staff/image-findings/events`, () =>
        HttpResponse.json({
          events: [{ ...initial, revision, title: revision === 1 ? 'Лекция' : 'Новое название' }],
        }),
      ),
      http.put(`${API}/staff/image-findings/events/${ID}`, async ({ request }) => {
        const body = (await request.json()) as { expected_revision: number };
        revisions.push(body.expected_revision);
        revision = 2;
        return HttpResponse.json({}, { status: 409 });
      }),
    );
    renderApp({ route: `/events/review/${ID}` });
    const title = await screen.findByLabelText('Название');
    await userEvent.type(title, ' — мои правки');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await screen.findByText(/Событие или источник изменились/);
    expect(title).toHaveValue('Лекция — мои правки');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    expect(revisions).toEqual([1, 1]);
    await userEvent.click(screen.getByRole('button', { name: 'Загрузить актуальную версию' }));
    await waitFor(() => expect(screen.getByLabelText('Название')).toHaveValue('Новое название'));
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() => expect(revisions).toEqual([1, 1, 2]));
  });
});

it('uses group timezone, every confirmed multi-day date and never guesses unknown dates', () => {
  const item: PublishedEvent = {
    id: ID,
    revision: 2,
    state: 'active',
    title: 'Фестиваль',
    source_availability: 'expired',
    retained_until: '2026-10-18T00:00:00Z',
    starts_at: '2026-10-05T23:30:00-02:00',
    ends_at: '2026-10-08T01:30:00+03:00',
    multi_day: true,
  };
  expect(eventDays(item, 'Europe/Moscow')).toEqual({ first: '2026-10-06', last: '2026-10-08' });
  expect(groupEventOnDay(item, '2026-10-05', 'Europe/Moscow')).toBe(false);
  for (const day of ['2026-10-06', '2026-10-07', '2026-10-08'])
    expect(groupEventOnDay(item, day, 'Europe/Moscow')).toBe(true);
  expect(groupEventOnDay({ ...item, state: 'cancelled' }, '2026-10-07', 'Europe/Moscow')).toBe(false);
  expect(eventDays({ title: 'Без даты' }, 'Europe/Moscow')).toBeNull();
  expect(eventDate({ title: 'День', starts_on: '2026-10-06' }, 'America/Los_Angeles')).toBe('6 окт');
});

it('uses an exclusive timestamp end but displays its actual midnight date', () => {
  const item = {
    title: 'Вечер',
    starts_at: '2026-10-05T20:00:00+03:00',
    ends_at: '2026-10-06T00:00:00+03:00',
    multi_day: true,
  };
  expect(eventDays(item, 'Europe/Moscow')).toEqual({ first: '2026-10-05', last: '2026-10-05' });
  expect(eventDate(item, 'Europe/Moscow')).toBe('5 окт · 20:00 — 6 окт · 00:00');
});


it('shows an offline state when the saved-event request cannot reach the server', async () => {
  useScenario('events');
  const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  try {
    server.use(http.get(`${API}/group-events`, () => HttpResponse.error()));
    renderApp({ route: '/events' });
    expect(await screen.findByRole('heading', { name: 'Нет соединения' }, { timeout: 6000 })).toBeInTheDocument();
  } finally {
    online.mockRestore();
  }
}, 10000);
