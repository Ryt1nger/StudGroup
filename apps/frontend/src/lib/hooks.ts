import { useEffect, useState, useSyncExternalStore } from 'react';

function subscribeOnline(callback: () => void) {
  window.addEventListener('online', callback);
  window.addEventListener('offline', callback);
  return () => {
    window.removeEventListener('online', callback);
    window.removeEventListener('offline', callback);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
}

/** Re-renders on an interval so relative labels ("stale") stay correct. */
export function useTick(intervalMs = 30_000): number {
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return tick;
}

/**
 * Server-corrected "now": device clocks are not trusted for deadlines or staleness.
 * `serverTimeIso` is the `server_time` of the response received at `receivedAtMs`.
 */
export function useServerNow(serverTimeIso: string | undefined, receivedAtMs: number, intervalMs = 30_000): number {
  const deviceNow = useTick(intervalMs);
  if (!serverTimeIso) return deviceNow;
  const offset = Date.parse(serverTimeIso) - receivedAtMs;
  return deviceNow + offset;
}
