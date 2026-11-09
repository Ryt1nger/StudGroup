import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, renderApp, server, useScenario } from './helpers';

const legal = { version: '2026-11-08-v1', ready: false, terms: false, service: false, sdo: false };

function stubOnboarding(role: 'student' | 'headman' = 'student') {
  let step = role === 'student' ? 'welcome' : 'personal';
  let completed = false;
  const calls: string[] = [];
  server.use(
    http.get(API + '/onboarding', () => HttpResponse.json({
      role, step, completed, group_name: 'БИ 1.2', academic_group_code: null,
      legal_required: false,
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
  );
  return { calls, getStep: () => step, isComplete: () => completed };
}

describe('resumable Mini App onboarding', () => {
  it('keeps the Telegram-linked student on the saved step after closing and reopening', async () => {
    useScenario();
    const state = stubOnboarding();
    const first = renderApp();
    expect(await screen.findByText(/Твоя учёба/)).toBeInTheDocument();
    expect(screen.getByText('Шаг 1 из 6')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Начать знакомство/ }));
    expect(await screen.findByText('Сначала о данных')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    expect(await screen.findByText('Давай знакомиться')).toBeInTheDocument();
    expect(state.getStep()).toBe('personal');

    first.unmount();
    renderApp();
    expect(await screen.findByText('Давай знакомиться')).toBeInTheDocument();
    expect(screen.getByText('Шаг 3 из 6')).toBeInTheDocument();
    await userEvent.type(await screen.findByRole('textbox', { name: /Имя/ }), 'Анна');
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    expect(await screen.findByText('Твои подгруппы')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Продолжить/ }));
    expect(await screen.findByText('СДО и личный кабинет')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Пропустить' }));
    expect(await screen.findByText('Всё готово!')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Открыть StudGroup/ }));
    await waitFor(() => expect(state.isComplete()).toBe(true));
    await waitFor(() => expect(screen.queryByText('Всё готово!')).not.toBeInTheDocument());
    expect(state.calls).toEqual(['legal', 'personal', 'subgroups', 'sdo', 'finish']);
  });

  it('gives a new headman one profile form instead of the student carousel', async () => {
    useScenario();
    const state = stubOnboarding('headman');
    renderApp();
    expect(await screen.findByText('Добро пожаловать!')).toBeInTheDocument();
    expect(screen.queryByText('Шаг 1 из 6')).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: /Имя/ }), 'Андрей');
    await userEvent.click(screen.getByRole('button', { name: /Завершить настройку/ }));
    await waitFor(() => expect(state.isComplete()).toBe(true));
    expect(state.calls).toEqual(['finish']);
  });

  it('does not open Mini App onboarding without a verified active group', async () => {
    useScenario('no-group');
    renderApp();
    await waitFor(() => expect(screen.queryByText('Давай знакомиться')).not.toBeInTheDocument());
    expect(screen.queryByText('Твоя учёба.')).not.toBeInTheDocument();
  });
});
