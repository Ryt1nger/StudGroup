import type { HomeworkDetail } from '@studgroup/shared-types';
import { contractExamples } from '@studgroup/shared-types/examples';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, renderApp, server, useScenario } from './helpers';

const HW = '44444444-4444-4444-8444-444444444444';
const EV = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const base = contractExamples.HomeworkDetailAvailable as unknown as HomeworkDetail;
const editable = { ...base, permissions: [...base.permissions, 'homework.edit', 'homework.material.write'] };

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
const eventResponse = (permissions: string[], item = event) => ({
  generated_at: new Date().toISOString(),
  group_timezone: 'Europe/Moscow',
  item,
  permissions,
});

describe('headman card editing', () => {
  it('opens the edit form on its own page and returns to the card after saving', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.patch(`${API}/homework/${HW}`, () => HttpResponse.json({ ...editable, title: 'Другое название' })),
    );
    const { router } = renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('link', { name: 'Редактировать' }));
    expect(router.state.location.pathname).toBe(`/homework/${HW}/edit`);
    expect(await screen.findByRole('heading', { name: /Редактирование/, level: 1 })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Название'), '!');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/homework/${HW}`));
  });

  it('shows no form on the edit page to a student who opens the address directly', async () => {
    useScenario();
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(base)));
    renderApp({ route: `/homework/${HW}/edit` });
    expect(await screen.findByText('У вас нет прав редактировать эту карточку.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
  });

  it('hides the edit button from a student', async () => {
    useScenario();
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(base)));
    renderApp({ route: `/homework/${HW}` });
    await screen.findByText(base.title);
    expect(screen.queryByRole('link', { name: 'Редактировать' })).not.toBeInTheDocument();
  });

  it('sends only the changed fields with the current revision and shows the saved card', async () => {
    useScenario();
    let sent: unknown = null;
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.patch(`${API}/homework/${HW}`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ ...editable, title: 'Новое название', revision: base.revision + 1 });
      }),
    );
    renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('link', { name: 'Редактировать' }));
    const title = screen.getByLabelText('Название');
    await userEvent.clear(title);
    await userEvent.type(title, 'Новое название');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(sent).toEqual({ expected_revision: base.revision, title: 'Новое название' }));
    expect(await screen.findByText('Новое название')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
  });

  it('sets a date with time in the group timezone as an offset-aware instant', async () => {
    useScenario();
    let sent: { deadline?: { at: string; date_only: boolean } } = {};
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.patch(`${API}/homework/${HW}`, async ({ request }) => {
        sent = (await request.json()) as typeof sent;
        return HttpResponse.json(editable);
      }),
    );
    renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('link', { name: 'Редактировать' }));
    await userEvent.click(screen.getByRole('radio', { name: 'Дата и время' }));
    await userEvent.clear(screen.getByLabelText('Дата'));
    await userEvent.type(screen.getByLabelText('Дата'), '2026-11-02');
    await userEvent.clear(screen.getByLabelText('Время'));
    await userEvent.type(screen.getByLabelText('Время'), '10:30');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(sent.deadline).toEqual({ at: '2026-11-02T07:30:00.000Z', date_only: false }));
  });

  it('refuses an empty title without calling the server', async () => {
    useScenario();
    let calls = 0;
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.patch(`${API}/homework/${HW}`, () => {
        calls += 1;
        return HttpResponse.json(editable);
      }),
    );
    renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('link', { name: 'Редактировать' }));
    await userEvent.clear(screen.getByLabelText('Название'));
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('название');
    expect(calls).toBe(0);
  });

  it('explains a revision conflict in words and keeps the form open', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.patch(`${API}/homework/${HW}`, () =>
        HttpResponse.json(
          {
            error: {
              code: 'revision_conflict',
              message: 'x',
              correlation_id: 'c',
              retryable: false,
              retry_after_seconds: null,
              details: null,
            },
          },
          { status: 409 },
        ),
      ),
    );
    renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('link', { name: 'Редактировать' }));
    await userEvent.type(screen.getByLabelText('Название'), '!');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    const alert = await screen.findByRole('alert');
    expect(alert).not.toHaveTextContent('revision_conflict');
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
  });

  it('edits an academic event, including its kind', async () => {
    useScenario();
    let sent: unknown = null;
    server.use(
      http.get(`${API}/deadlines/${EV}`, () => HttpResponse.json(eventResponse(['homework.edit']))),
      http.patch(`${API}/deadlines/${EV}`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(eventResponse(['homework.edit'], { ...event, kind: 'exam', revision: 4 }));
      }),
    );
    renderApp({ route: `/deadlines/${EV}` });
    await userEvent.click(await screen.findByRole('link', { name: 'Редактировать' }));
    await userEvent.selectOptions(screen.getByLabelText('Вид'), 'exam');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(sent).toEqual({ expected_revision: 3, kind: 'exam' }));
  });

  it('does not offer event editing without the permission', async () => {
    useScenario();
    server.use(http.get(`${API}/deadlines/${EV}`, () => HttpResponse.json(eventResponse([]))));
    renderApp({ route: `/deadlines/${EV}` });
    await screen.findAllByText('КТ 1: эссе');
    expect(screen.queryByRole('link', { name: 'Редактировать' })).not.toBeInTheDocument();
  });

  it('opens the bot to send a file, and hides the button from students', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.get(`${API}/homework/${HW}/file-link`, () =>
        HttpResponse.json({ url: `https://t.me/studgroup_rf_bot?start=file_h_${HW}` }),
      ),
    );
    const { adapter } = renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить файл' }));
    await waitFor(() =>
      expect(adapter?.openTelegramLink).toHaveBeenCalledWith(
        `https://t.me/studgroup_rf_bot?start=file_h_${HW}`,
      ),
    );
  });

  it('refuses to open a deep link that is not on t.me', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json(editable)),
      http.get(`${API}/homework/${HW}/file-link`, () => HttpResponse.json({ url: 'https://evil.example/x' })),
    );
    const { adapter } = renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить файл' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Добавить файл' })).toBeEnabled());
    expect(adapter?.openTelegramLink).not.toHaveBeenCalled();
  });

  it('opens a Telegram-kept file through the API origin and never as a relative page link', async () => {
    useScenario();
    const withFile = {
      ...base,
      materials: [
        {
          id: 'm1',
          title: 'Лекция 3.pdf',
          url: '/v1/files/m1?exp=1&sig=abc',
          kind: 'document',
          file_name: 'Лекция 3.pdf',
          mime_type: 'application/pdf',
          size_bytes: 1000,
        },
      ],
    };
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(withFile)));
    renderApp({ route: `/homework/${HW}` });
    const link = await screen.findByRole('link', { name: 'Лекция 3.pdf' });
    expect(link).toHaveAttribute('href', 'https://api.studgroup.example/v1/files/m1?exp=1&sig=abc');
  });
});
