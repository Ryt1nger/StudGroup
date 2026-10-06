import { useSession } from './SessionProvider';

/** Group timezone (IANA) from the authenticated session; the device timezone is not authoritative. */
export function useGroupTimeZone(): string {
  const { state } = useSession();
  return (state.status === 'ready' ? state.session.group?.timezone : undefined) ?? 'UTC';
}
