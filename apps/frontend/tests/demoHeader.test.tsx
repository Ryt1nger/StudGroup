import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { createMockAdapter } from '../src/telegram/mockAdapter';
import { createLocalPreviewAdapter } from '../src/telegram/localPreviewAdapter';
import { resetDemoNotifications } from '../src/shell/demo/demoStore';
import { API, server, renderApp, useScenario } from './helpers';

describe('mock-only header actions (bell + avatar)', () => {
  beforeEach(() => {
    resetDemoNotifications();
    useScenario();
  });
  afterEach(() => vi.unstubAllEnvs());

  it('shows real header actions outside mock mode', async () => {
    renderApp();
    await screen.findByText('ДЗ №12–140');
    expect(screen.getByRole('button', { name: /Уведомления/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Профиль/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Профиль/ }));
    expect(within(screen.getByRole('dialog')).queryByText('Демо')).not.toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await userEvent.click(screen.getByRole('button', { name: /Уведомления/ }));
    expect(await within(screen.getByRole('dialog')).findByText('Пока уведомлений нет')).toBeInTheDocument();
  });

  it('reads production inbox changes and persists mark-all through the API', async () => {
    const item = { id: 'n1', title: 'Математика', body: 'Срок задания изменён',
      entity_type: 'homework', entity_id: 'h1', created_at: '2026-10-07T10:00:00Z', read: false };
    let marked = false;
    const result = () => ({ items: [{ ...item, read: marked }], unread_count: marked ? 0 : 1, generated_at: '2026-10-07T10:00:00Z' });
    server.use(http.get(`${API}/notifications`, () => HttpResponse.json(result())),
      http.post(`${API}/notifications/read-all`, () => { marked = true; return HttpResponse.json(result()); }));
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Уведомления, есть непрочитанные/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(item.body)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Отметить все прочитанными' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Уведомления' })).toBeInTheDocument());
    expect(marked).toBe(true);
  });

  it('switches the real profile theme and restores it after reopening', async () => {
    const view = renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Профиль/ }));
    const toggle = screen.getByRole('switch', { name: 'Тёмная тема' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.cookie).toContain('studgroup_theme=dark');
    view.unmount();
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Профиль/ }));
    expect(screen.getByRole('switch', { name: 'Тёмная тема' })).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(screen.getByRole('switch', { name: 'Тёмная тема' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.cookie).toContain('studgroup_theme=light');
  });

  it('ignores an invalid saved appearance and starts with the Telegram theme', async () => {
    document.cookie = 'studgroup_theme=invalid; Path=/';
    renderApp({ adapter: createMockAdapter({ scheme: 'dark', startParam: null, hasInitData: true }) });
    await userEvent.click(await screen.findByRole('button', { name: /Профиль/ }));
    expect(screen.getByRole('switch', { name: 'Тёмная тема' })).toHaveAttribute('aria-checked', 'true');
  });

  it('shows the bell with an unread dot and the avatar with test-user initials', async () => {
    vi.stubEnv('VITE_API_MOCK', 'true');
    renderApp();
    expect(await screen.findByRole('button', { name: /Уведомления \(демо\), есть непрочитанные/ })).toBeInTheDocument();
    expect(screen.getByTestId('demo-unread-dot')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Профиль \(демо\): Анна/ })).toHaveTextContent('А');
  });

  it('shows preview header actions without enabling mock API mode', async () => {
    vi.stubEnv('VITE_API_MOCK', 'false');
    vi.stubEnv('VITE_LOCAL_PREVIEW', 'true');
    vi.stubEnv('VITE_LOCAL_PREVIEW_CREDENTIAL', 'local-test-only');
    const adapter = createLocalPreviewAdapter();
    expect(adapter.isMock).toBe(false);
    renderApp({ adapter });
    await userEvent.click(await screen.findByRole('button', { name: /Профиль/ }));
    const dialog = screen.getByRole('dialog', { name: 'Профиль' });
    expect(within(dialog).getByText('Демо')).toBeInTheDocument();
    const toggle = within(dialog).getByRole('switch', { name: 'Тёмная тема' });
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(adapter.isMock).toBe(false);
  });

  it('notification panel is labelled «Демо»; the red dot disappears only once everything is read', async () => {
    vi.stubEnv('VITE_API_MOCK', 'true');
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Уведомления/ }));
    const dialog = screen.getByRole('dialog', { name: 'Уведомления' });
    expect(within(dialog).getByText('Демо')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /Срок задания изменился, непрочитано/ }));
    expect(screen.getByTestId('demo-unread-dot')).toBeInTheDocument(); // one is still unread
    await userEvent.click(within(dialog).getByRole('button', { name: 'Отметить все прочитанными' }));
    expect(screen.queryByTestId('demo-unread-dot')).not.toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    // Closing is animated: the panel stays mounted briefly, then goes away.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('profile panel is labelled «Демо» and shows the test user', async () => {
    vi.stubEnv('VITE_API_MOCK', 'true');
    renderApp();
    await userEvent.click(await screen.findByRole('button', { name: /Профиль/ }));
    const dialog = screen.getByRole('dialog', { name: 'Профиль' });
    expect(within(dialog).getByText('Демо')).toBeInTheDocument();
    expect(within(dialog).getByText('Анна')).toBeInTheDocument();
  });

  it('profile panel switches the theme through the mock adapter and back', async () => {
    vi.stubEnv('VITE_API_MOCK', 'true');
    const adapter = createMockAdapter({ scheme: 'light', startParam: null, hasInitData: true });
    renderApp({ adapter });
    await userEvent.click(await screen.findByRole('button', { name: /Профиль/ }));
    const toggle = within(screen.getByRole('dialog', { name: 'Профиль' })).getByRole('switch', { name: 'Тёмная тема' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(document.documentElement.dataset.theme).toBe('dark');
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('closing fades the panel out before unmounting it and returns focus to the trigger', async () => {
    vi.stubEnv('VITE_API_MOCK', 'true');
    renderApp();
    const avatar = await screen.findByRole('button', { name: /Профиль/ });
    await userEvent.click(avatar);
    const dialog = screen.getByRole('dialog', { name: 'Профиль' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Закрыть' }));
    // Still mounted while the exit animation runs.
    expect(screen.getByRole('dialog', { name: 'Профиль' }).closest('div')!.className).toContain('leaving');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(avatar).toHaveFocus();
  });
});
