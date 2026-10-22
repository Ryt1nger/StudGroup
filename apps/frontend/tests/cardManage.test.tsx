import type { HomeworkDetail } from '@studgroup/shared-types';
import { contractExamples } from '@studgroup/shared-types/examples';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { exampleSession, sessionAt } from '../src/mocks/data';
import { API, renderApp, server, useScenario } from './helpers';

const HW = '44444444-4444-4444-8444-444444444444';
const EV = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const NEW_HW = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const NEW_EV = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const base = contractExamples.HomeworkDetailAvailable as unknown as HomeworkDetail;
const editable = { ...base, permissions: [...base.permissions, 'homework.edit', 'homework.material.write'] };

/** The session of a headman: the staff permissions are only a UI hint, the server decides. */
function asHeadman() {
  server.use(
    http.post(`${API}/session/bootstrap`, () =>
      HttpResponse.json(
        sessionAt(exampleSession, Date.now(), (session) => ({
          ...session,
          permissions: [...session.permissions, 'homework.create'],
        })),
      ),
    ),
  );
}

const event = {
  id: EV,
  kind: 'control_point',
  subject: 'Основы российской государственности',
  title: 'КТ 1: эссе',
  description: '1000–1500 слов',
  deadline_at: null,
  date_only: false,
  window_start: null,
  window_end: null,
  date_hint: null,
  needs_clarification: false,
  source_message_ids: [],
  revision: 3,
};
const eventResponse = (item = event) => ({
  generated_at: new Date().toISOString(),
  group_timezone: 'Europe/Moscow',
  item,
  permissions: ['homework.edit', 'homework.material.write'],
});

describe('the «+» button', () => {
  it('is offered to a headman and opens the form for the open tab', async () => {
    useScenario();
    asHeadman();
    const { router } = renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить карточку' }));
    expect(router.state.location.pathname).toBe('/cards/new');
    expect(router.state.location.search).toBe('');
  });

  it('preselects an event when pressed on the deadlines tab', async () => {
    useScenario();
    asHeadman();
    const { router } = renderApp({ route: '/deadlines' });
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить карточку' }));
    expect(router.state.location.search).toBe('?type=event');
    expect(await screen.findByLabelText('Вид')).toBeInTheDocument();
  });

  it('is not shown to a student', async () => {
    useScenario();
    renderApp({ route: '/tasks' });
    await screen.findByRole('heading', { name: 'Задания', level: 1 });
    expect(screen.queryByRole('button', { name: 'Добавить карточку' })).not.toBeInTheDocument();
  });

  it('shows a student who opens the address directly no form', async () => {
    useScenario();
    renderApp({ route: '/cards/new' });
    expect(await screen.findByRole('alert')).toHaveTextContent('староста и помощник');
    expect(screen.queryByRole('button', { name: 'Создать' })).not.toBeInTheDocument();
  });
});

describe('creating a card', () => {
  it('creates a homework card from the filled fields and opens it', async () => {
    useScenario();
    asHeadman();
    let sent: unknown = null;
    server.use(
      http.post(`${API}/homework`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ ...editable, id: NEW_HW, title: 'Номера 1–5' }, { status: 201 });
      }),
      http.get(`${API}/homework/${NEW_HW}`, () =>
        HttpResponse.json({ ...editable, id: NEW_HW, title: 'Номера 1–5' }),
      ),
    );
    const { router } = renderApp({ route: '/cards/new' });
    await userEvent.type(await screen.findByLabelText('Название'), 'Номера 1–5');
    await userEvent.type(screen.getByLabelText('Предмет'), 'Математика');
    await userEvent.type(screen.getByLabelText('Описание'), 'Решить письменно');
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/homework/${NEW_HW}`));
    expect(sent).toEqual({ title: 'Номера 1–5', subject: 'Математика', description: 'Решить письменно' });
  });

  it('sends the deadline as an offset-aware instant in the group timezone', async () => {
    useScenario();
    asHeadman();
    let sent: { deadline?: unknown } = {};
    server.use(
      http.post(`${API}/homework`, async ({ request }) => {
        sent = (await request.json()) as typeof sent;
        return HttpResponse.json({ ...editable, id: NEW_HW }, { status: 201 });
      }),
    );
    renderApp({ route: '/cards/new' });
    await userEvent.type(await screen.findByLabelText('Название'), 'Задача');
    await userEvent.type(screen.getByLabelText('Предмет'), 'Математика');
    await userEvent.click(screen.getByRole('radio', { name: 'Дата и время' }));
    await userEvent.type(screen.getByLabelText('Дата'), '2026-11-02');
    await userEvent.type(screen.getByLabelText('Время'), '10:30');
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));
    await waitFor(() => expect(sent.deadline).toEqual({ at: '2026-11-02T07:30:00.000Z', date_only: false }));
  });

  it('creates an event with its kind and a date hint', async () => {
    useScenario();
    asHeadman();
    let sent: unknown = null;
    server.use(
      http.post(`${API}/deadlines`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(eventResponse({ ...event, id: NEW_EV }), { status: 201 });
      }),
      http.get(`${API}/deadlines/${NEW_EV}`, () =>
        HttpResponse.json(eventResponse({ ...event, id: NEW_EV })),
      ),
    );
    const { router } = renderApp({ route: '/cards/new?type=event' });
    await userEvent.selectOptions(await screen.findByLabelText('Вид'), 'test');
    await userEvent.type(screen.getByLabelText('Название'), 'Тест по пределам');
    await userEvent.type(screen.getByLabelText('Предмет'), 'Математика');
    await userEvent.type(screen.getByLabelText('Пояснение к сроку'), 'в среду на паре');
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/deadlines/${NEW_EV}`));
    expect(sent).toEqual({
      title: 'Тест по пределам',
      subject: 'Математика',
      kind: 'test',
      date_hint: 'в среду на паре',
    });
  });

  it('switches between a task and an event without keeping the other form', async () => {
    useScenario();
    asHeadman();
    renderApp({ route: '/cards/new' });
    expect(screen.queryByLabelText('Вид')).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole('radio', { name: 'Событие' }));
    expect(await screen.findByLabelText('Вид')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: 'Задание' }));
    expect(screen.queryByLabelText('Вид')).not.toBeInTheDocument();
  });

  it('refuses a missing title or subject without calling the server', async () => {
    useScenario();
    asHeadman();
    let calls = 0;
    server.use(
      http.post(`${API}/homework`, () => {
        calls += 1;
        return HttpResponse.json(editable, { status: 201 });
      }),
    );
    renderApp({ route: '/cards/new' });
    await userEvent.click(await screen.findByRole('button', { name: 'Создать' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('название');
    await userEvent.type(screen.getByLabelText('Название'), 'Задача');
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('предмет');
    expect(calls).toBe(0);
  });

  it('keeps the form and explains a failure when the server says no', async () => {
    useScenario();
    asHeadman();
    server.use(
      http.post(`${API}/homework`, () =>
        HttpResponse.json(
          { error: { code: 'permission_denied', message: 'x', correlation_id: 'c', retryable: false } },
          { status: 403 },
        ),
      ),
    );
    const { router } = renderApp({ route: '/cards/new' });
    await userEvent.type(await screen.findByLabelText('Название'), 'Задача');
    await userEvent.type(screen.getByLabelText('Предмет'), 'Математика');
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('нет прав');
    expect(router.state.location.pathname).toBe('/cards/new');
    expect(screen.getByLabelText('Название')).toHaveValue('Задача');
  });
});

describe('removing a card', () => {
  it('asks first, then removes the homework card and returns to the list', async () => {
    useScenario();
    let url = '';
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.delete(`${API}/homework/${HW}`, ({ request }) => {
        url = request.url;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { router } = renderApp({ route: `/homework/${HW}/edit` });
    await userEvent.click(await screen.findByRole('button', { name: 'Удалить карточку' }));
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent('пропадёт у всех студентов');
    expect(url).toBe('');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Не удалять' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Удалить карточку' }));
    await userEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Да, удалить' }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(new URL(url).searchParams.get('expected_revision')).toBe(String(base.revision));
  });

  it('removes an event and returns to the deadlines', async () => {
    useScenario();
    let url = '';
    server.use(
      http.get(`${API}/deadlines/${EV}`, () => HttpResponse.json(eventResponse())),
      http.delete(`${API}/deadlines/${EV}`, ({ request }) => {
        url = request.url;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { router } = renderApp({ route: `/deadlines/${EV}/edit` });
    await userEvent.click(await screen.findByRole('button', { name: 'Удалить карточку' }));
    await userEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Да, удалить' }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/deadlines'));
    expect(new URL(url).searchParams.get('expected_revision')).toBe('3');
  });

  it('explains a revision conflict and keeps the card', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.delete(`${API}/homework/${HW}`, () =>
        HttpResponse.json(
          { error: { code: 'revision_conflict', message: 'x', correlation_id: 'c', retryable: true } },
          { status: 409 },
        ),
      ),
    );
    const { router } = renderApp({ route: `/homework/${HW}/edit` });
    await userEvent.click(await screen.findByRole('button', { name: 'Удалить карточку' }));
    await userEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Да, удалить' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('уже изменили');
    expect(router.state.location.pathname).toBe(`/homework/${HW}/edit`);
  });

  it('offers no removal to a student who opens the edit address', async () => {
    useScenario();
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(base)));
    renderApp({ route: `/homework/${HW}/edit` });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Удалить карточку' })).not.toBeInTheDocument();
  });
});
