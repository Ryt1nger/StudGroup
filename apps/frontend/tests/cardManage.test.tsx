import type { HomeworkDetail } from '@studgroup/shared-types';
import { contractExamples } from '@studgroup/shared-types/examples';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { exampleSession, sessionAt } from '../src/mocks/data';
import { API, renderApp, server, useScenario } from './helpers';

const HW = '44444444-4444-4444-8444-444444444444';
const UNDO = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const NEW_HW = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
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

describe('creating and removing cards', () => {
  it('offers «+» to a headman only', async () => {
    useScenario();
    const { router } = renderApp({ route: '/tasks' });
    await screen.findByRole('group', { name: 'Фильтр заданий' });
    expect(screen.queryByRole('link', { name: 'Новая карточка' })).not.toBeInTheDocument();
    router.dispose();
  });

  it('creates a homework card from the «+» form and opens it', async () => {
    useScenario();
    asHeadman();
    let sent: unknown = null;
    server.use(
      http.post(`${API}/homework`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ ...base, id: NEW_HW, title: 'Номера 1–5' }, { status: 201 });
      }),
      http.get(`${API}/homework/${NEW_HW}`, () => HttpResponse.json({ ...editable, id: NEW_HW, title: 'Номера 1–5' })),
    );
    const { router } = renderApp({ route: '/tasks' });
    await userEvent.click(await screen.findByRole('link', { name: 'Новая карточка' }));
    expect(router.state.location.pathname).toBe('/cards/new');
    await userEvent.type(await screen.findByLabelText('Название'), 'Номера 1–5');
    await userEvent.type(screen.getByLabelText('Предмет'), 'Математический анализ');
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));
    await waitFor(() => expect(sent).toEqual({ title: 'Номера 1–5', subject: 'Математический анализ' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/homework/${NEW_HW}`));
  });

  it('removes a card only after the confirmation, with the revision the card has', async () => {
    useScenario();
    let revision: string | null = null;
    let restored = false;
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.delete(`${API}/homework/${HW}`, ({ request }) => {
        revision = new URL(request.url).searchParams.get('expected_revision');
        return HttpResponse.json({ undo_id: UNDO });
      }),
      http.post(`${API}/card-removals/${UNDO}/undo`, () => {
        restored = true;
        return HttpResponse.json({ entity_type: 'homework', entity_id: HW });
      }),
    );
    const { router } = renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('button', { name: 'Удалить карточку' }));
    expect(revision).toBeNull();
    await userEvent.click(await screen.findByRole('button', { name: 'Не удалять' }));
    expect(revision).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Удалить карточку' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Да, удалить' }));
    await waitFor(() => expect(revision).toBe(String(base.revision)));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    // The bar with the countdown offers to take the removal back.
    await userEvent.click(await screen.findByRole('button', { name: 'Отменить' }));
    await waitFor(() => expect(restored).toBe(true));
    expect(await screen.findByText('Карточка возвращена')).toBeInTheDocument();
  });

  it('hides the trash button from a student', async () => {
    useScenario();
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(base)));
    renderApp({ route: `/homework/${HW}` });
    await screen.findByText(base.title);
    expect(screen.queryByRole('button', { name: 'Удалить карточку' })).not.toBeInTheDocument();
  });
});
