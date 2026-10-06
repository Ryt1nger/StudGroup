import { isApiRequestError } from './errors';

/**
 * Cache policy. Learning data lives only in TanStack Query's in-memory cache for the current
 * run (owner decision): no persistQueryClient, no storage, no Service Worker.
 */
const MAX_RETRIES = 2;

export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  if (!isApiRequestError(error)) return false;
  if (error.status === 401 || error.status === 403 || error.status === 404 || error.status === 409 || error.status === 410) return false;
  return error.retryable;
}

export function retryDelay(attempt: number, error: unknown): number {
  const after = isApiRequestError(error) ? error.retryAfterSeconds : null;
  if (after) return after * 1000;
  return Math.min(1000 * 2 ** attempt, 8000);
}

export const todayQueryOptions = {
  staleTime: 30_000,
  gcTime: 5 * 60_000,
  retry: shouldRetry,
  retryDelay,
  networkMode: 'always' as const,
  refetchOnWindowFocus: true,
  refetchOnReconnect: true,
};

export const detailQueryOptions = {
  staleTime: 30_000,
  gcTime: 5 * 60_000,
  retry: shouldRetry,
  retryDelay,
  networkMode: 'always' as const,
  refetchOnWindowFocus: true,
  refetchOnReconnect: true,
};

/** Processing polling (owner decision Q16): 10 s, then 30 s, stop after 10 min. */
export const PROCESSING_POLL = { firstDelayMs: 10_000, laterDelayMs: 30_000, giveUpAfterMs: 10 * 60_000 } as const;

/** Data is "possibly stale" after 15 minutes without a successful refresh (owner decision Q12). */
export const STALE_AFTER_MS = 15 * 60_000;

export const scheduleQueryOptions = {
  staleTime: 60_000,
  gcTime: 5 * 60_000,
  retry: shouldRetry,
  retryDelay,
  networkMode: 'always' as const,
  refetchOnWindowFocus: true,
  refetchOnReconnect: true,
};

/**
 * Cursor-paginated list (Tasks). It never refetches by itself: refetching an infinite query replays old
 * cursors, which expire (15 min) and freeze selection time. The list is restarted explicitly instead
 * (filter change, mutation, stale return) – see features/tasks/useTaskList.ts.
 */
export const listQueryOptions = {
  staleTime: Infinity,
  gcTime: 5 * 60_000,
  retry: shouldRetry,
  retryDelay,
  networkMode: 'always' as const,
  refetchOnMount: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
};

/** A returning user sees the cached list only if its first page is this fresh; otherwise it restarts. */
export const LIST_RESTART_AFTER_MS = 30_000;
