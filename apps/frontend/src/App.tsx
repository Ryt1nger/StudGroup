import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { RouterProvider } from 'react-router';
import { AccessGate } from './session/AccessGate';
import { OnboardingGate } from './features/onboarding/OnboardingGate';
import { SessionProvider } from './session/SessionProvider';
import type { TelegramAdapter } from './telegram/adapter';
import { TelegramProvider } from './telegram/TelegramProvider';
import { createAppRouter } from './router';

export function App({ adapter, router: injectedRouter }: { adapter: TelegramAdapter | null; router?: ReturnType<typeof createAppRouter> }) {
  // The active QueryClient is memory-only. A small encrypted academic snapshot is
  // restored by SessionProvider *after* the backend verifies the current student.
  const [queryClient] = useState(() => new QueryClient());
  const [router] = useState(() => injectedRouter ?? createAppRouter(adapter?.startParam ?? null));
  return (
    <TelegramProvider adapter={adapter}>
      <QueryClientProvider client={queryClient}>
        <SessionProvider>
          {window.location.pathname.startsWith('/legal/') ? (
            <RouterProvider router={router} />
          ) : (
            <AccessGate>
              <OnboardingGate><RouterProvider router={router} /></OnboardingGate>
            </AccessGate>
          )}
        </SessionProvider>
      </QueryClientProvider>
    </TelegramProvider>
  );
}
