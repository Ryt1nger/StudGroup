import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { contractExamples } from '@studgroup/shared-types/examples';
import { API, makeAdapter, renderApp, server, useScenario } from './helpers';
import { buildNextLesson } from '../src/mocks/data';

const HW = '44444444-4444-4444-8444-444444444444';
const HW_UNAVAILABLE = '66666666-6666-4666-8666-666666666666';

describe('session and access', () => {
  it('shows the Today feed after bootstrap and keeps the token out of browser storage', async () => {
    useScenario();
    renderApp();
    expect(await screen.findByRole('heading', { name: 'Сегодня', level: 1 })).toBeInTheDocument();
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
    // eslint-disable-next-line no-restricted-properties -- the test asserts that nothing is persisted
    expect(window.localStorage.length).toBe(0);
    // eslint-disable-next-line no-restricted-properties -- the test asserts that nothing is persisted
    expect(window.sessionStorage.length).toBe(0);
  });

  it('asks to open the app from Telegram when initData is missing', async () => {
    useScenario();
    renderApp({ adapter: null });
    expect(await screen.findByText('Откройте StudGroup из Telegram')).toBeInTheDocument();
  });

  it('requires reopening the Mini App when the session/initData has expired (no refresh)', async () => {
    useScenario('expired');
    renderApp();
    expect(await screen.findByText('Сессия истекла')).toBeInTheDocument();
    expect(screen.getByText('Откройте приложение заново из Telegram.')).toBeInTheDocument();
  });

  it('shows «Нет активной группы» for users without a group', async () => {
    useScenario('no-group');
    renderApp();
    expect(await screen.findByText('Нет активной группы')).toBeInTheDocument();
  });

  it('offers a membership recheck only when the session allows it', async () => {
    useScenario('suspended');
    renderApp();
    expect(await screen.findByText('Не удалось подтвердить участие')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Проверить ещё раз' }));
    expect(await screen.findByRole('heading', { name: 'Сегодня', level: 1 })).toBeInTheDocument();
  });

  it('treats a 401 on a data request as an ended session', async () => {
    useScenario();
    server.use(http.get(`${API}/today`, () => HttpResponse.json({ error: { code: 'session_expired', message: 'x', correlation_id: 'c', retryable: false } }, { status: 401 })));
    renderApp();
    expect(await screen.findByText('Сессия истекла')).toBeInTheDocument();
  });
});

describe('Today', () => {
  it('opens the subject page from the lesson and opens related homework', async () => {
    useScenario();
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Следующая пара:/ }));
    expect(await screen.findByRole('heading', { name: 'Математический анализ', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Домашние задания' })).toBeInTheDocument();
    expect(screen.getByText('События и КТ пока не подключены.')).toBeInTheDocument();
    expect(screen.getByText('Материалы пока не подключены.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: /ДЗ №12–140/ }));
    expect(await screen.findByText('Что нужно сделать')).toBeInTheDocument();
  });
  it('shows the finished-day notice and never substitutes a tomorrow lesson', async () => {
    useScenario();
    server.use(http.get(`${API}/today`, () => HttpResponse.json({
      ...contractExamples.TodayPopulated,
      server_time: '2026-10-05T15:00:00+03:00',
      day_lessons_state: 'finished',
      next_lesson: buildNextLesson(Date.parse('2026-10-06T06:00:00Z'), 'upcoming'),
    })));
    renderApp();
    expect(await screen.findByText('На сегодня пары закончились')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Следующая пара:/ })).not.toBeInTheDocument();
  });
  it('switches the heading and requests tomorrow without assigning unknown deadlines', async () => {
    useScenario();
    renderApp();
    await screen.findByText('ДЗ №12–140');
    const todayButton = screen.getByRole('button', { name: 'Сегодня' });
    const tomorrowButton = screen.getByRole('button', { name: 'Завтра' });
    await userEvent.click(tomorrowButton);
    expect(await screen.findByRole('heading', { name: 'Завтра', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Завтра' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Завтра' })).toBe(tomorrowButton);
    expect(screen.getByRole('button', { name: 'Сегодня' })).toBe(todayButton);
    await waitFor(() => expect(screen.queryByText('ДЗ №12–140')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Сегодня' }));
    expect(await screen.findByRole('heading', { name: 'Сегодня', level: 1 })).toBeInTheDocument();
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
  });
  it('renders backend-ordered sections, urgency only from the field, and hides undefined badges', async () => {
    useScenario();
    renderApp();
    await screen.findByText('ДЗ №12–140');
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(['Срок сегодня', 'Новое и изменённое', 'Ближайшие']);
    // urgent badge appears for urgent/super_urgent cards only
    expect(screen.getAllByText('Срочно')).toHaveLength(1);
    expect(screen.queryByText(/Найти или спросить/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Уведомления/ })).toBeInTheDocument();
    // Completed cards with active deadlines stay visible, including meaningful updates.
    expect(screen.getByText('Эссе по маркетингу')).toBeInTheDocument();
    expect(screen.getByText('Обновлено — проверьте')).toBeInTheDocument();
    // processing/source/verification details are not on list cards
    expect(screen.queryByText('Проверяем обновление')).not.toBeInTheDocument();
    expect(screen.queryByText('Из сообщения группы')).not.toBeInTheDocument();
    expect(screen.queryByText('Подтверждено')).not.toBeInTheDocument();
    expect(screen.queryByText('ДЗ')).not.toBeInTheDocument();
    // unknown deadline + needs_clarification: date line and the primary label are both kept
    expect(screen.getByText('Срок уточняется', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('Требует уточнения')).toBeInTheDocument();
  });

  it('shows the empty state for a successfully loaded empty feed', async () => {
    useScenario('empty');
    renderApp();
    expect(await screen.findByText('На сегодня заданий нет')).toBeInTheDocument();
  });

  it('shows the delayed-processing text without inventing cards', async () => {
    useScenario('delayed');
    renderApp();
    expect(await screen.findByText('Обработка информации задерживается. Данные появятся автоматически')).toBeInTheDocument();
    expect(screen.getByText(/Данные могли устареть · обновлено в/)).toBeInTheDocument();
    expect(screen.queryByText('На сегодня заданий нет')).not.toBeInTheDocument();
  });

  it('shows the feed-level update indicator', async () => {
    useScenario('updating');
    renderApp();
    expect(await screen.findByText('Обновляем информацию группы')).toBeInTheDocument();
  });

  it('shows a retryable service error and recovers', async () => {
    useScenario('service-error');
    renderApp();
    expect(await screen.findByText('Сервис временно недоступен', {}, { timeout: 6000 })).toBeInTheDocument();
    useScenario();
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText('ДЗ №12–140')).toBeInTheDocument();
  }, 15000);

  it('shows a permission-denied state', async () => {
    useScenario('forbidden');
    renderApp();
    expect(await screen.findByText('Нет доступа')).toBeInTheDocument();
  });
});

describe('prepared sections', () => {
  it.each(['/subjects'])('%s says «Раздел готовится»', async (route) => {
    useScenario();
    renderApp({ route });
    expect(await screen.findByText('Раздел готовится')).toBeInTheDocument();
  });
});

describe('homework detail and source', () => {
  it('opens a card from Today and the source through the Telegram adapter', async () => {
    useScenario();
    const { adapter } = renderApp();
    await userEvent.click(await screen.findByRole('link', { name: /ДЗ №12–140/ }));
    expect(await screen.findByText('Что нужно сделать')).toBeInTheDocument();
    expect(screen.queryByText('Фрагмент')).not.toBeInTheDocument();
    expect(screen.queryByRole('blockquote')).not.toBeInTheDocument();
    expect(screen.queryByText('По матану решаем номера 12–140.')).not.toBeInTheDocument();
    // Source details are collapsed behind the info button in the hero card.
    const info = screen.getByRole('button', { name: 'Информация об источнике' });
    expect(info).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById(info.getAttribute('aria-controls')!)).toHaveAttribute('aria-hidden', 'true');
    await userEvent.click(info);
    expect(info).toHaveAttribute('aria-expanded', 'true');
    const panel = document.getElementById(info.getAttribute('aria-controls')!)!;
    expect(panel).toHaveAttribute('aria-hidden', 'false');
    expect(within(panel).getByText('Подтверждено')).toBeInTheDocument();
    expect(within(panel).getByText(/Получено/)).toBeInTheDocument();
    await userEvent.click(info);
    expect(info).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(screen.getByRole('button', { name: /Сообщение учебной группы/ }));
    expect(adapter?.openTelegramLink).toHaveBeenCalledWith('https://t.me/c/1234567890/42');
  });

  it('opens a card directly from a deep link start_param', async () => {
    useScenario();
    renderApp({ route: '/', adapter: makeAdapter({ startParam: `hw_${HW.replaceAll('-', '')}` }) });
    expect(await screen.findByText('Что нужно сделать')).toBeInTheDocument();
  });

  it('keeps the card and explains an unavailable source calmly', async () => {
    useScenario();
    renderApp({ route: `/homework/${HW_UNAVAILABLE}` });
    expect(await screen.findByText('Подготовить презентацию')).toBeInTheDocument();
    expect(screen.getByText('Источник недоступен')).toBeInTheDocument();
    expect(screen.getAllByText('Срок уточняется').length).toBeGreaterThan(0);
    expect(screen.getByText('Требует уточнения')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('never opens a non-t.me URL even if the backend returns one', async () => {
    useScenario();
    const bad = { ...(contractExamples.HomeworkDetailAvailable as object), source_detail: { ...contractExamples.HomeworkDetailAvailable.source_detail, action: { type: 'open_telegram_link', url: 'https://evil.example/x' } } };
    server.use(http.get(`${API}/homework/${HW}`, () => HttpResponse.json(bad)));
    const { adapter } = renderApp({ route: `/homework/${HW}` });
    expect(await screen.findByText('Источник недоступен')).toBeInTheDocument();
    expect(adapter?.openTelegramLink).not.toHaveBeenCalled();
  });

  it('shows not-found and gone states for missing or expired cards', async () => {
    useScenario();
    const first = renderApp({ route: '/homework/00000000-0000-4000-8000-000000000404' });
    expect(await screen.findByText('Задание не найдено')).toBeInTheDocument();
    first.unmount();
    renderApp({ route: '/homework/00000000-0000-4000-8000-000000000410' });
    expect(await screen.findByText('Данные больше не хранятся')).toBeInTheDocument();
  });
});

describe('personal completion «Выполнено мной»', () => {
  it('marks and unmarks completion', async () => {
    useScenario();
    renderApp({ route: `/homework/${HW}` });
    const mark = await screen.findByRole('button', { name: 'Выполнено мной' });
    await userEvent.click(mark);
    await waitFor(() => expect(screen.getByRole('button', { name: /Снять отметку/ })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: /Снять отметку/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Выполнено мной' })).toBeEnabled());
  });

  it('rolls back and reports a failed write', async () => {
    useScenario('completion-fails');
    renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('button', { name: 'Выполнено мной' }));
    expect(await screen.findByText('Не удалось сохранить отметку. Попробуйте ещё раз.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Выполнено мной' })).toBeInTheDocument();
  });

  it('refetches the card after a revision conflict, then the repeated action succeeds', async () => {
    useScenario('completion-conflict');
    renderApp({ route: `/homework/${HW}` });
    await userEvent.click(await screen.findByRole('button', { name: 'Выполнено мной' }));
    expect(await screen.findByText(/Данные задания изменились/)).toBeInTheDocument();
    // The refetch brings the new revision; only then can the action be repeated.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Выполнено мной' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Выполнено мной' }));
    expect(await screen.findByRole('button', { name: /Снять отметку/ })).toBeInTheDocument();
    expect(screen.queryByText(/Данные задания изменились/)).not.toBeInTheDocument();
  });

  it('hides the action without the backend permission', async () => {
    useScenario();
    server.use(
      http.get(`${API}/homework/${HW}`, () => HttpResponse.json({ ...contractExamples.HomeworkDetailAvailable, permissions: ['homework.read'] })),
    );
    renderApp({ route: `/homework/${HW}` });
    await screen.findByText('Что нужно сделать');
    expect(screen.queryByRole('button', { name: 'Выполнено мной' })).not.toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Основная навигация' });
    expect(within(nav).getAllByRole('link')).toHaveLength(4);
  });
});
