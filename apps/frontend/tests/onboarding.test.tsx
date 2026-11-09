import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, renderApp, server, useScenario } from './helpers';

const legal = { version: '2026-11-09-v2', ready: true, credentials_ready: false, terms: false, service: false, sdo: false };

function stubOnboarding(role: 'student' | 'headman' = 'student') {
  let step = role === 'student' ? 'welcome' : 'personal';
  let completed = false;
  const calls: string[] = [];
  server.use(
    http.get(API + '/onboarding', () => HttpResponse.json({
      role, step, completed, group_name: 'БИ 1.2', academic_group_code: null, legal_required: true,
    })),
    http.put(API + '/onboarding/progress', async ({ request }) => {
      const body = (await request.json()) as { step: string };
      calls.push(body.step);
      step = body.step;
      return HttpResponse.json({ step, completed: false });
    }),
    http.post(API + '/onboarding/complete', () => {
      completed = true;
      step = 'complete';
      return HttpResponse.json({ step, completed: true });
    }),
    http.get(API + '/subgroups', () => HttpResponse.json({ items: [] })),
    http.get(API + '/legal/status', () => HttpResponse.json(legal)),
    http.post(API + '/legal/consents', () => HttpResponse.json({ granted: true })),
  );
  return { calls, getStep: () => step, isComplete: () => completed };
}

async function advancePastPersonal() {
  const first = await screen.findByRole('textbox', { name: /Имя/ });
  if (!('value' in first) || !(first as HTMLInputElement).value) await userEvent.type(first, 'Анна');
  await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
  expect(await screen.findByText('Контакты для связи')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
  expect(await screen.findByText('Подгруппы по предметам')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /Сохранить и продолжить/ }));
  expect(await screen.findByText('СДО и личный кабинет')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /Подключить позже/ }));
  expect(await screen.findByText('Почти готово')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('checkbox', { name: /Принимаю условия/ }));
  await userEvent.click(screen.getByRole('checkbox', { name: /Даю согласие/ }));
  await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
  expect(await screen.findByText('Подстроим приложение под вас')).toBeInTheDocument();
}

describe('premium membership-scoped onboarding', () => {
  it('keeps the legal step blocked while documents are unpublished', async () => {
    useScenario();
    stubOnboarding();
    server.use(http.get(API + '/legal/status', () => HttpResponse.json({ ...legal, ready: false })));
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Начать настройку/ }));
    const first = await screen.findByRole('textbox', { name: /Имя/ });
    if (!(first as HTMLInputElement).value) await userEvent.type(first, 'Анна');
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Далее/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Сохранить и продолжить/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Подключить позже/ }));
    expect(await screen.findByText('Почти готово')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Принимаю условия/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Даю согласие/ })).not.toBeChecked();
    expect(screen.getByRole('button', { name: /Продолжить/ })).toBeDisabled();
    expect(screen.getByText(/Документы пока находятся на утверждении/)).toBeInTheDocument();
  });
  it('resumes from the server, uses the profile appearance controls and never repeats once completed', async () => {
    useScenario();
    const state = stubOnboarding();
    const first = renderApp();
    expect(await screen.findByText('Настроим StudGroup под вас')).toBeInTheDocument();
    expect(screen.getByLabelText('Шаг 1 из 8')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Начать настройку/ }));
    expect(await screen.findByText('Как к вам обращаться?')).toBeInTheDocument();
    first.unmount();

    const second = renderApp();
    expect(await screen.findByText('Как к вам обращаться?')).toBeInTheDocument();
    expect(screen.getByLabelText('Шаг 2 из 8')).toBeInTheDocument();
    await advancePastPersonal();

    await userEvent.click(screen.getByRole('button', { name: 'Бирюзовый' }));
    await userEvent.click(screen.getByRole('button', { name: 'Тёмная' }));
    await userEvent.click(screen.getByRole('button', { name: 'Крупный' }));
    await userEvent.click(screen.getByRole('button', { name: /Сохранить оформление/ }));
    expect(await screen.findByText('Настройка завершена!')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Перейти в StudGroup/ }));
    await waitFor(() => expect(state.isComplete()).toBe(true));
    await waitFor(() => expect(screen.queryByText('Настройка завершена!')).not.toBeInTheDocument());
    expect(state.calls).toEqual(['personal', 'contacts', 'subgroups', 'sdo', 'legal', 'appearance', 'finish']);
    second.unmount();

    renderApp();
    await waitFor(() => expect(state.getStep()).toBe('complete'));
    expect(screen.queryByText('Настроим StudGroup под вас')).not.toBeInTheDocument();
    expect(screen.queryByText('Как к вам обращаться?')).not.toBeInTheDocument();
  });

  it('gives a headman the same multi-step design without requiring a subgroup choice', async () => {
    useScenario();
    const state = stubOnboarding('headman');
    renderApp();
    expect(await screen.findByText('Настроим StudGroup под вас')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Начать настройку/ }));
    expect(await screen.findByText('Как к вам обращаться?')).toBeInTheDocument();
    await advancePastPersonal();
    await userEvent.click(screen.getByRole('button', { name: /Сохранить оформление/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Перейти в StudGroup/ }));
    await waitFor(() => expect(state.isComplete()).toBe(true));
    expect(state.calls).toEqual(['contacts', 'subgroups', 'sdo', 'legal', 'appearance', 'finish']);
  });


  it('stores optional SDO credentials only after the separate approvals', async () => {
    useScenario();
    stubOnboarding();
    const receipts: string[] = [];
    let credentialWrites = 0;
    server.use(
      http.get(API + '/legal/status', () => HttpResponse.json({ ...legal, ready: true, credentials_ready: true })),
      http.post(API + '/legal/consents', async ({ request }) => {
        const { purpose } = (await request.json()) as { purpose: string };
        receipts.push(purpose);
        return HttpResponse.json({ purpose, granted: true });
      }),
    );
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Начать настройку/ }));
    const first = await screen.findByRole('textbox', { name: /Имя/ });
    if (!(first as HTMLInputElement).value) await userEvent.type(first, 'Анна');
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    expect(await screen.findByText('Контакты для связи')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    expect(await screen.findByText('Подгруппы по предметам')).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: /Сохранить и продолжить/ }));
    expect(await screen.findByText('СДО и личный кабинет')).toBeInTheDocument();

    // Intercept credential writes after the ordinary profile fields have been saved.
    server.use(http.patch(API + '/profile', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      if (body.sdu_login) credentialWrites += 1;
      return HttpResponse.json({
        first_name: 'Анна', last_name: null, email: null,
        sdu: { platform: 'РАНХиГС — СДО / ЛК', login: body.sdu_login ?? null, password_set: true },
        settings: { enabled: true },
      });
    }));

    const login = screen.getByRole('textbox', { name: /Логин СДО/ });
    await waitFor(() => expect(login).toBeEnabled());
    await userEvent.clear(login);
    await userEvent.type(login, 'student-login');
    await userEvent.type(screen.getByLabelText('Пароль СДО/ЛК'), 'example-password');
    await userEvent.click(screen.getByRole('checkbox', { name: /Разрешаю хранить реквизиты СДО/ }));
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    expect(await screen.findByText('Почти готово')).toBeInTheDocument();
    expect(credentialWrites).toBe(0);
    expect(receipts).toHaveLength(0);
    await userEvent.click(screen.getByRole('checkbox', { name: /Принимаю условия/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: /Даю согласие/ }));
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    await waitFor(() => expect(credentialWrites).toBe(1));
    expect(receipts).toEqual(['terms', 'service', 'sdo']);
    expect(await screen.findByText('Подстроим приложение под вас')).toBeInTheDocument();
  });

  it('shows at most two subject groups at a time and persists every choice', async () => {
    useScenario();
    stubOnboarding();
    const choices = new Map<string, string>();
    const subjects = ['Английский язык', 'Информатика', 'Физическая культура', 'Лабораторный практикум'];
    const getItems = () => subjects.map((name, index) => ({
      split_id: String(index + 1), subject: { id: String(index + 1), name },
      options: [{ id: 'one', name: '1' }, { id: 'two', name: '2' }],
      selected_subgroup_id: choices.get(String(index + 1)) ?? null,
    }));
    server.use(
      http.get(API + '/subgroups', () => HttpResponse.json({ items: getItems() })),
      http.put(API + '/subgroups/:splitId', async ({ params, request }) => {
        const body = (await request.json()) as { subgroup_id: string };
        choices.set(String(params.splitId), body.subgroup_id);
        return HttpResponse.json({ items: getItems() });
      }),
    );
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Начать настройку/ }));
    const name = await screen.findByRole('textbox', { name: /Имя/ });
    if (!(name as HTMLInputElement).value) await userEvent.type(name, 'Мария');
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    await screen.findByText('Контакты для связи');
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    await screen.findByText('Подгруппы по предметам');
    expect(await screen.findByText('Английский язык')).toBeInTheDocument();
    expect(screen.getByText('Информатика')).toBeInTheDocument();
    expect(screen.queryByText('Физическая культура')).not.toBeInTheDocument();
    const nextSubjects = screen.getByRole('button', { name: /Следующие предметы/ });
    expect(nextSubjects).toBeDisabled();

    for (const subject of subjects.slice(0, 2)) {
      const group = screen.getByRole('group', { name: 'Подгруппа: ' + subject });
      await userEvent.click(within(group).getByRole('button', { name: '2' }));
      await waitFor(() => expect(within(group).getByRole('button', { name: '2' })).toHaveAttribute('aria-pressed', 'true'));
    }
    await userEvent.click(nextSubjects);
    expect(await screen.findByText('Физическая культура')).toBeInTheDocument();
    expect(screen.getByText('Лабораторный практикум')).toBeInTheDocument();
    expect(screen.queryByText('Английский язык')).not.toBeInTheDocument();

    for (const subject of subjects.slice(2)) {
      const group = screen.getByRole('group', { name: 'Подгруппа: ' + subject });
      await userEvent.click(within(group).getByRole('button', { name: '1' }));
      await waitFor(() => expect(within(group).getByRole('button', { name: '1' })).toHaveAttribute('aria-pressed', 'true'));
    }
    await userEvent.click(screen.getByRole('button', { name: /Сохранить и продолжить/ }));
    expect(await screen.findByText('СДО и личный кабинет')).toBeInTheDocument();
    expect([...choices.entries()]).toEqual([['1', 'two'], ['2', 'two'], ['3', 'one'], ['4', 'one']]);
  });

  it('never shows onboarding outside a verified active membership', async () => {
    useScenario('no-group');
    renderApp();
    await waitFor(() => expect(screen.queryByText('Настроим StudGroup под вас')).not.toBeInTheDocument());
  });


  it('lets a completed member preview all screens without any registration or profile writes', async () => {
    useScenario();
    let writes = 0;
    server.use(
      http.get(API + '/onboarding', () => HttpResponse.json({
        role: 'headman', step: 'complete', completed: true, group_name: 'БИ 1.2',
        academic_group_code: null, legal_required: false,
      })),
      http.put(API + '/onboarding/progress', () => { writes += 1; return HttpResponse.json({ completed: true }); }),
      http.post(API + '/onboarding/complete', () => { writes += 1; return HttpResponse.json({ completed: true }); }),
      http.patch(API + '/profile', () => { writes += 1; return HttpResponse.json({}); }),
      http.post(API + '/legal/consents', () => { writes += 1; return HttpResponse.json({}); }),
      http.put(API + '/subgroups/:splitId', () => { writes += 1; return HttpResponse.json({}); }),
    );
    renderApp({ route: '/profile' });
    const open = await screen.findByRole('button', { name: 'Посмотреть первый запуск' });
    await userEvent.click(open);
    expect(await screen.findByText('Настроим StudGroup под вас')).toBeInTheDocument();
    expect(screen.getByText(/Предпросмотр · данные не сохраняются/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Начать настройку/ }));
    expect(await screen.findByText('Как к вам обращаться?')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: /Фамилия/ }), 'Предпросмотр');
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    expect(await screen.findByText('Контакты для связи')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Далее/ }));
    expect(await screen.findByText('Подгруппы по предметам')).toBeInTheDocument();
    expect(screen.getByText('Пример: 2 предмета')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Сохранить и продолжить/ }));
    expect(await screen.findByText('СДО и личный кабинет')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Пароль')).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    expect(await screen.findByText('Почти готово')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /Принимаю условия/ }));
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    expect(await screen.findByText('Подстроим приложение под вас')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Тёмная' }));
    await userEvent.click(screen.getByRole('button', { name: 'Бирюзовый' }));
    await userEvent.click(screen.getByRole('button', { name: 'Крупный' }));
    expect(screen.getByRole('button', { name: 'Тёмная' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: /Сохранить оформление/ }));
    expect(await screen.findByText('Настройка завершена!')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Вернуться в приложение/ }));
    expect(await screen.findByRole('button', { name: 'Посмотреть первый запуск' })).toBeInTheDocument();
    expect(writes).toBe(0);
    // Reopening is an explicit action; it does not change completed server state.
    await userEvent.click(screen.getByRole('button', { name: 'Посмотреть первый запуск' }));
    expect(await screen.findByText('Настроим StudGroup под вас')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Закрыть/ }));
    expect(await screen.findByRole('button', { name: 'Посмотреть первый запуск' })).toBeInTheDocument();
    expect(writes).toBe(0);
  });

  it('does not flash registration for a server-completed membership', async () => {
    useScenario();
    server.use(http.get(API + '/onboarding', () => HttpResponse.json({
      role: 'headman', step: 'complete', completed: true, group_name: 'БИ 1.2',
      academic_group_code: null, legal_required: false,
    })));
    renderApp();
    expect(screen.queryByText('Настроим StudGroup под вас')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Настроим StudGroup под вас')).not.toBeInTheDocument());
  });
});
