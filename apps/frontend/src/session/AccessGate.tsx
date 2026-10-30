import { useState, type ReactNode } from 'react';
import { ru } from '../i18n/ru';
import { StateButton, StateView } from '../ui/Blocks';
import { useSession as useSessionGate } from './SessionProvider';

function Screen({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <div className="scroll"><div className="view" data-dir="none">{children}</div></div>
    </div>
  );
}

function Splash() {
  return (
    <div className="app">
      <div className="gate">
        <div className="splash" role="status" aria-live="polite">
          <div className="logo" aria-hidden="true">SG</div>
          <p>{ru.gate.bootLoading}</p>
          <div className="sk" style={{ width: '60%', height: 14, borderRadius: 8 }} />
          <div className="sk" style={{ width: '40%', height: 14, borderRadius: 8 }} />
        </div>
      </div>
    </div>
  );
}

/** Renders the right screen for every session / membership state; children only for `active`. */
export function AccessGate({ children }: { children: ReactNode }) {
  const { state, retry, recheck } = useSessionGate();
  const [rechecking, setRechecking] = useState(false);
  const [recheckFailed, setRecheckFailed] = useState(false);

  if (state.status === 'loading') return <Splash />;

  if (state.status === 'no-telegram') {
    return <Screen><StateView icon="lock" title={ru.gate.notTelegramTitle} body={ru.gate.notTelegramBody} /></Screen>;
  }
  if (state.status === 'expired') {
    return <Screen><StateView icon="lock" title={ru.gate.expiredTitle} body={ru.gate.expiredBody} /></Screen>;
  }
  if (state.status === 'failed') {
    return (
      <Screen>
        <StateView
          icon={state.offline ? 'wifioff' : 'alert'}
          title={state.offline ? ru.states.offlineTitle : ru.states.serviceTitle}
          body={state.offline ? ru.states.offlineBody : ru.states.serviceBody}
          code={state.correlationId}
          action={<StateButton icon="refresh" onClick={retry}>{ru.states.retry}</StateButton>}
        />
      </Screen>
    );
  }

  const { session } = state;
  switch (session.access_state) {
    case 'active':
      return <>{children}</>;
    case 'membership_suspended': {
      const canRecheck = session.membership?.can_recheck === true;
      return (
        <Screen>
          <StateView
            icon="lock"
            title={ru.gate.suspendedTitle}
            body={ru.gate.suspendedBody}
            action={
              canRecheck ? (
                <StateButton
                  icon="refresh"
                  busy={rechecking}
                  onClick={() => {
                    setRechecking(true);
                    setRecheckFailed(false);
                    recheck()
                      .catch(() => setRecheckFailed(true))
                      .finally(() => setRechecking(false));
                  }}
                >
                  {ru.gate.recheck}
                </StateButton>
              ) : undefined
            }
          />
          {recheckFailed ? <p className="rfail" role="alert" style={{ textAlign: 'center' }}>{ru.states.errorBody}</p> : null}
        </Screen>
      );
    }
    case 'membership_left':
      return <Screen><StateView icon="lock" title={ru.gate.leftTitle} body={ru.gate.leftBody} /></Screen>;
    case 'group_deletion_pending':
      return <Screen><StateView icon="lock" title={ru.gate.deletionTitle} body={ru.gate.deletionBody} /></Screen>;
    case 'no_active_group':
    default:
      // Unknown future access states fail closed to the safe "no active group" screen.
      return <Screen><StateView icon="lock" title={ru.gate.noGroupTitle} body={ru.gate.noGroupBody} /></Screen>;
  }
}
