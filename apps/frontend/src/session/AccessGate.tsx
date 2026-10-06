import { useState, type ReactNode } from 'react';
import { assets } from '../assets';
import { ru } from '../i18n/ru';
import { StateView } from '../ui/StateView';
import { Button } from '../ui/Button';
import { Skeleton } from '../ui/Skeleton';
import { ThemedImage } from '../ui/ThemedImage';
import { AlertCircle, Lock, Refresh, WifiOff } from '../ui/icons';
import { useSession } from './SessionProvider';
import styles from './AccessGate.module.css';

function Splash() {
  return (
    <div className={styles.splash} role="status" aria-live="polite">
      <ThemedImage asset={assets.logoSymbol} className={styles.splashLogo} />
      <p className={styles.splashText}>{ru.gate.bootLoading}</p>
      <div className={styles.splashBars} aria-hidden="true">
        <Skeleton height={14} width="60%" />
        <Skeleton height={14} width="40%" />
      </div>
    </div>
  );
}

/** Renders the right screen for every session / membership state; children only for `active`. */
export function AccessGate({ children }: { children: ReactNode }) {
  const { state, retry, recheck } = useSession();
  const [rechecking, setRechecking] = useState(false);
  const [recheckFailed, setRecheckFailed] = useState(false);

  if (state.status === 'loading') return <Splash />;

  if (state.status === 'no-telegram') {
    return (
      <div className={styles.page}>
        <StateView icon={<Lock />} title={ru.gate.notTelegramTitle} body={ru.gate.notTelegramBody} />
      </div>
    );
  }

  if (state.status === 'expired') {
    return (
      <div className={styles.page}>
        <StateView role="alert" icon={<Lock />} title={ru.gate.expiredTitle} body={ru.gate.expiredBody} />
      </div>
    );
  }

  if (state.status === 'failed') {
    return (
      <div className={styles.page}>
        <StateView
          role="alert"
          icon={state.offline ? <WifiOff /> : <AlertCircle />}
          title={state.offline ? ru.states.offlineTitle : ru.states.serviceTitle}
          body={state.offline ? ru.states.offlineBody : ru.states.serviceBody}
          correlationId={state.correlationId}
          action={
            <Button icon={<Refresh />} onClick={retry}>
              {ru.states.retry}
            </Button>
          }
        />
      </div>
    );
  }

  const { session } = state;
  switch (session.access_state) {
    case 'active':
      return <>{children}</>;
    case 'membership_suspended': {
      const canRecheck = session.membership?.can_recheck === true;
      return (
        <div className={styles.page}>
          <StateView
            icon={<Lock />}
            title={ru.gate.suspendedTitle}
            body={ru.gate.suspendedBody}
            action={
              canRecheck ? (
                <Button
                  busy={rechecking}
                  icon={<Refresh />}
                  onClick={() => {
                    setRechecking(true);
                    setRecheckFailed(false);
                    recheck()
                      .catch(() => setRecheckFailed(true))
                      .finally(() => setRechecking(false));
                  }}
                >
                  {ru.gate.recheck}
                </Button>
              ) : undefined
            }
          />
          {recheckFailed ? <p className={styles.note}>{ru.states.errorBody}</p> : null}
        </div>
      );
    }
    case 'membership_left':
      return (
        <div className={styles.page}>
          <StateView icon={<Lock />} title={ru.gate.leftTitle} body={ru.gate.leftBody} />
        </div>
      );
    case 'group_deletion_pending':
      return (
        <div className={styles.page}>
          <StateView icon={<Lock />} title={ru.gate.deletionTitle} body={ru.gate.deletionBody} />
        </div>
      );
    case 'no_active_group':
    default:
      // Unknown future access states fail closed to the safe "no active group" screen.
      return (
        <div className={styles.page}>
          <StateView icon={<Lock />} title={ru.gate.noGroupTitle} body={ru.gate.noGroupBody} />
        </div>
      );
  }
}
