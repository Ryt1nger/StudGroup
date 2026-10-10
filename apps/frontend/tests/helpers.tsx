import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { vi } from 'vitest';
import { configureApiClient, setAccessToken } from '../src/api/client';
import { createHandlers } from '../src/mocks/handlers';
import type { Scenario } from '../src/mocks/scenarios';
import { createTestRouter } from '../src/router';
import { AccessGate } from '../src/session/AccessGate';
import { SessionProvider } from '../src/session/SessionProvider';
import type { TelegramAdapter } from '../src/telegram/adapter';
import { TelegramProvider } from '../src/telegram/TelegramProvider';
import { RouterProvider } from 'react-router';

export const API = 'https://api.studgroup.example/v1';
export const server = setupServer();

export function makeAdapter(
  overrides: Partial<TelegramAdapter> = {},
): TelegramAdapter & { openTelegramLink: ReturnType<typeof vi.fn> } {
  return {
    isTelegram: false,
    isMock: true,
    initData: 'test=1&hash=invalid',
    startParam: null,
    platform: 'test',
    supportsBackButton: false,
    colorScheme: 'light',
    ready: vi.fn(),
    onThemeChange: () => () => undefined,
    safeArea: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
    onViewportChange: () => () => undefined,
    showBackButton: () => null,
    openTelegramLink: vi.fn(),
    openExternalLink: vi.fn(),
    haptic: vi.fn(),
    ...overrides,
  } as TelegramAdapter & { openTelegramLink: ReturnType<typeof vi.fn> };
}

export function useScenario(scenario: Scenario = 'populated') {
  server.resetHandlers(...createHandlers(API, scenario));
}

export function renderApp(options: { route?: string; adapter?: TelegramAdapter | null } = {}) {
  configureApiClient();
  setAccessToken(null);
  const adapter = options.adapter === undefined ? makeAdapter() : options.adapter;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createTestRouter(adapter?.startParam ?? null, [options.route ?? '/today']);
  const view = render(
    <TelegramProvider adapter={adapter}>
      <QueryClientProvider client={queryClient}>
        <SessionProvider>
          <AccessGate>
            <RouterProvider router={router} />
          </AccessGate>
        </SessionProvider>
      </QueryClientProvider>
    </TelegramProvider>,
  );
  return { ...view, adapter, router, queryClient };
}
