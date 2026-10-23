import { useEffect, type ReactNode } from 'react';
import { Link } from 'react-router';
import { isApiRequestError } from '../api/errors';
import { ru } from '../i18n/ru';
import { useOnline } from '../lib/hooks';
import { useSession } from '../session/SessionProvider';
import { StateButton, StateView } from './Blocks';

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
    <StateButton icon="refresh" onClick={onRetry}>{ru.states.retry}</StateButton>
  ) : undefined;
  const toToday: ReactNode = homeLink ? (
    <Link className="btn on sbtn" to="/today" replace>{ru.states.toToday}</Link>
  ) : undefined;

  if (!isApiRequestError(error)) {
    return <StateView icon="alert" title={ru.states.errorTitle} body={ru.states.errorBody} action={retry} />;
  }
  if (error.kind === 'network') {
    return online ? (
      <StateView icon="alert" title={ru.states.serviceTitle} body={ru.states.serviceBody} action={retry} />
    ) : (
      <StateView icon="wifioff" title={ru.states.offlineTitle} body={ru.states.offlineBody} action={retry} />
    );
  }
  switch (status) {
    case 401:
      return null; // session screen takes over
    case 403:
      return <StateView icon="lock" title={ru.states.forbiddenTitle} body={ru.states.forbiddenBody} code={error.correlationId} action={toToday} />;
    case 404:
      return <StateView icon="alert" title={ru.states.notFoundTitle} body={ru.states.notFoundBody} action={toToday} />;
    case 410:
      return <StateView icon="alert" title={ru.states.goneTitle} body={ru.states.goneBody} action={toToday} />;
    case 429:
      return <StateView icon="alert" title={ru.states.errorTitle} body={ru.states.rateLimited} action={retry} />;
    default:
      return (
        <StateView
          icon="alert"
          title={status !== null && status >= 500 ? ru.states.serviceTitle : ru.states.errorTitle}
          body={status !== null && status >= 500 ? ru.states.serviceBody : ru.states.errorBody}
          code={error.correlationId}
          action={retry}
        />
      );
  }
}
