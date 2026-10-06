import { useEffect, type ReactNode } from 'react';
import { Link } from 'react-router';
import { isApiRequestError } from '../api/errors';
import { ru } from '../i18n/ru';
import { useOnline } from '../lib/hooks';
import { useSession } from '../session/SessionProvider';
import { Button } from './Button';
import { StateView } from './StateView';
import { AlertCircle, Lock, Refresh, WifiOff } from './icons';

interface Props {
  error: unknown;
  onRetry?: () => void;
  /** Where the "back" action goes for terminal states (not found / gone / forbidden). */
  homeLink?: boolean;
}

/** Maps any request failure to a usable state. A 401 ends the session. */
export function RequestError({ error, onRetry, homeLink = false }: Props) {
  const online = useOnline();
  const { markExpired } = useSession();
  const status = isApiRequestError(error) ? error.status : null;

  useEffect(() => {
    if (status === 401) markExpired();
  }, [status, markExpired]);

  const retry: ReactNode = onRetry ? (
    <Button icon={<Refresh />} onClick={onRetry}>
      {ru.states.retry}
    </Button>
  ) : undefined;
  const toToday: ReactNode = homeLink ? (
    <Link to="/today" replace>
      <Button variant="secondary">{ru.states.toToday}</Button>
    </Link>
  ) : undefined;

  if (!isApiRequestError(error)) {
    return <StateView role="alert" icon={<AlertCircle />} title={ru.states.errorTitle} body={ru.states.errorBody} action={retry} />;
  }
  if (error.kind === 'network') {
    return online ? (
      <StateView role="alert" icon={<AlertCircle />} title={ru.states.serviceTitle} body={ru.states.serviceBody} action={retry} />
    ) : (
      <StateView role="alert" icon={<WifiOff />} title={ru.states.offlineTitle} body={ru.states.offlineBody} action={retry} />
    );
  }
  switch (status) {
    case 401:
      return null; // session screen takes over
    case 403:
      return <StateView role="alert" icon={<Lock />} title={ru.states.forbiddenTitle} body={ru.states.forbiddenBody} correlationId={error.correlationId} action={toToday} />;
    case 404:
      return <StateView role="alert" icon={<AlertCircle />} title={ru.states.notFoundTitle} body={ru.states.notFoundBody} action={toToday} />;
    case 410:
      return <StateView role="alert" icon={<AlertCircle />} title={ru.states.goneTitle} body={ru.states.goneBody} action={toToday} />;
    case 429:
      return <StateView role="alert" icon={<AlertCircle />} title={ru.states.errorTitle} body={ru.states.rateLimited} action={retry} />;
    default:
      return (
        <StateView
          role="alert"
          icon={<AlertCircle />}
          title={status !== null && status >= 500 ? ru.states.serviceTitle : ru.states.errorTitle}
          body={status !== null && status >= 500 ? ru.states.serviceBody : ru.states.errorBody}
          correlationId={error.correlationId}
          action={retry}
        />
      );
  }
}
