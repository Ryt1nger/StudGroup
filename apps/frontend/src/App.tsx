import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { RouterProvider } from 'react-router';
import { AccessGate } from './session/AccessGate';
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
          <AccessGate>
            <RouterProvider router={router} />
          </AccessGate>
        </SessionProvider>
      </QueryClientProvider>
    </TelegramProvider>
  );
}
