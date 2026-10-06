import { useCallback, useEffect, useReducer, useRef } from 'react';
import { PROCESSING_POLL } from '../api/cachePolicy';

interface PollState {
  gaveUp: boolean;
  /** Bumped by `restart` to re-arm the schedule. */
  epoch: number;
}

type PollAction = { type: 'give-up' } | { type: 'restart' } | { type: 'reset' };

function reducer(state: PollState, action: PollAction): PollState {
  switch (action.type) {
    case 'give-up':
      return state.gaveUp ? state : { ...state, gaveUp: true };
    case 'restart':
      return { gaveUp: false, epoch: state.epoch + 1 };
    case 'reset':
      return state.gaveUp ? { ...state, gaveUp: false } : state;
  }
}

/**
 * Polls while backend processing is not idle (owner decision Q16): first retry after 10 s,
 * then every 30 s, giving up after 10 minutes — then the UI offers «Проверить снова».
 */
export function useProcessingPoll(active: boolean, refetch: () => Promise<unknown>) {
  const [state, dispatch] = useReducer(reducer, { gaveUp: false, epoch: 0 });
  const refetchRef = useRef(refetch);
  useEffect(() => {
    refetchRef.current = refetch;
  });

  useEffect(() => {
    if (!active) {
      dispatch({ type: 'reset' });
      return;
    }
    const startedAt = Date.now();
    let timer: number | undefined;
    let first = true;
    let cancelled = false;
    const schedule = () => {
      if (cancelled) return;
      if (Date.now() - startedAt >= PROCESSING_POLL.giveUpAfterMs) {
        dispatch({ type: 'give-up' });
        return;
      }
      const delay = first ? PROCESSING_POLL.firstDelayMs : PROCESSING_POLL.laterDelayMs;
      first = false;
      timer = window.setTimeout(() => {
        if (document.visibilityState === 'hidden') {
          schedule();
          return;
        }
        void refetchRef.current().finally(schedule); // schedule() is a no-op after cleanup
      }, delay);
    };
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, state.epoch]);

  const restart = useCallback(() => {
    dispatch({ type: 'restart' });
    void refetchRef.current();
  }, []);

  return { gaveUp: state.gaveUp, restart };
}
