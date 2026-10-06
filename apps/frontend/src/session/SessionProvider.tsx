import type { SessionContext } from '@studgroup/shared-types';
import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/queries';
import { setAccessToken } from '../api/client';
import { isApiRequestError } from '../api/errors';
import { useTelegram } from '../telegram/TelegramProvider';

/** `session.server_time` anchored to the monotonic clock, so a wrong phone date cannot shift it. */
export interface ServerClock {
  serverMs: number;
  monotonicAtReceive: number;
}

function makeClock(session: SessionContext): ServerClock {
  return { serverMs: Date.parse(session.server_time), monotonicAtReceive: performance.now() };
}

export type SessionState =
  | { status: 'loading' }
  | { status: 'ready'; session: SessionContext; expiresAt: string; clock: ServerClock }
  /** The page is not inside Telegram (or initData is missing). */
  | { status: 'no-telegram' }
  /** 401 / expired initData / expired session: the Mini App must be reopened from Telegram. */
  | { status: 'expired' }
  | { status: 'failed'; offline: boolean; code: string | null; correlationId: string | null };

interface SessionContextValue {
  state: SessionState;
  retry: () => void;
  /** Re-check Telegram membership after a gate; updates the session on success. */
  recheck: () => Promise<void>;
  /** Called by data hooks when any request returns 401: the session is over. */
  markExpired: () => void;
}

const Ctx = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const { adapter } = useTelegram();
  const queryClient = useQueryClient();
  const [state, setState] = useState<SessionState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      if (!adapter || !adapter.initData) {
        setState({ status: 'no-telegram' });
        return;
      }
      setState({ status: 'loading' });
      try {
        const res = await api.bootstrap(adapter.initData);
        if (cancelled) return;
        setAccessToken(res.access_token);
        setState({ status: 'ready', session: res.session, expiresAt: res.expires_at, clock: makeClock(res.session) });
      } catch (error) {
        if (cancelled) return;
        if (isApiRequestError(error) && (error.status === 401 || error.code === 'initdata_expired' || error.code === 'initdata_invalid' || error.code === 'unauthenticated')) {
          setState({ status: 'expired' });
          return;
        }
        setState({
          status: 'failed',
          offline: isApiRequestError(error) && error.kind === 'network' && !navigator.onLine,
          code: isApiRequestError(error) ? error.code : null,
          correlationId: isApiRequestError(error) ? error.correlationId : null,
        });
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [adapter, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const markExpired = useCallback(() => {
    setAccessToken(null);
    queryClient.clear(); // learning data never outlives the session
    setState({ status: 'expired' });
  }, [queryClient]);

  const recheck = useCallback(async () => {
    const session = await api.recheck();
    setState((prev) => (prev.status === 'ready' ? { ...prev, session, clock: makeClock(session) } : prev));
    void queryClient.invalidateQueries();
  }, [queryClient]);

  const value = useMemo(() => ({ state, retry, recheck, markExpired }), [state, retry, recheck, markExpired]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSession(): SessionContextValue {
  const value = useContext(Ctx);
  if (!value) throw new Error('useSession must be used inside <SessionProvider>');
  return value;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useIsHeadman(): boolean {
  const value = useContext(Ctx);
  return value?.state.status === 'ready' && value.state.session.membership?.role === 'headman';
}

// eslint-disable-next-line react-refresh/only-export-components
export function useReadySession(): SessionContext {
  const { state } = useSession();
  if (state.status !== 'ready') throw new Error('useReadySession outside of a ready session');
  return state.session;
}

/** Server-based "now" from the session (null before a session is ready). */
// eslint-disable-next-line react-refresh/only-export-components
export function useSessionServerNow(): () => number | null {
  const { state } = useSession();
  return useCallback(() => {
    if (state.status !== 'ready') return null;
    return state.clock.serverMs + (performance.now() - state.clock.monotonicAtReceive);
  }, [state]);
}
