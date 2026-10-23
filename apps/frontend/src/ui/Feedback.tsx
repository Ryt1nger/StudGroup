import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

interface UndoState {
  id: number;
  text: string;
  action: string;
  onUndo: () => void;
}

interface FeedbackValue {
  toast: (message: string) => void;
  /** A small bar with a circular 5 s countdown and an undo button. */
  undo: (text: string, onUndo: () => void) => void;
  hasUndo: boolean;
}

const FeedbackContext = createContext<FeedbackValue | null>(null);
const UNDO_SECONDS = 5;

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Array<{ id: number; text: string }>>([]);
  const [undo, setUndo] = useState<UndoState | null>(null);
  const counter = useRef(0);

  const toast = useCallback((message: string) => {
    const id = ++counter.current;
    setToasts((current) => [...current, { id, text: message }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 1600);
  }, []);
  const showUndo = useCallback((text: string, onUndo: () => void) => {
    setUndo({ id: ++counter.current, text, action: 'Отменить', onUndo });
  }, []);
  const value = useMemo(() => ({ toast, undo: showUndo, hasUndo: undo !== null }), [toast, showUndo, undo]);

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      {toasts.map((item) => (
        <div key={item.id} className="toast" role="status">{item.text}</div>
      ))}
      {undo ? <UndoBar key={undo.id} state={undo} onClose={() => setUndo(null)} /> : null}
    </FeedbackContext.Provider>
  );
}

function UndoBar({ state, onClose }: { state: UndoState; onClose: () => void }) {
  const [left, setLeft] = useState(UNDO_SECONDS);
  useEffect(() => {
    const tick = window.setInterval(() => setLeft((value) => Math.max(1, value - 1)), 1000);
    const end = window.setTimeout(onClose, UNDO_SECONDS * 1000);
    return () => {
      window.clearInterval(tick);
      window.clearTimeout(end);
    };
  }, [onClose]);
  return (
    <div className="undo" role="status">
      <span className="ring">
        <svg viewBox="0 0 36 36" aria-hidden="true"><circle className="rt" cx="18" cy="18" r="15" /><circle className="rp" cx="18" cy="18" r="15" /></svg>
        <b>{left}</b>
      </span>
      <span className="ut">{state.text}</span>
      <button type="button" onClick={() => { state.onUndo(); onClose(); }}>{state.action}</button>
    </div>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFeedback(): FeedbackValue {
  const value = useContext(FeedbackContext);
  if (!value) throw new Error('useFeedback must be used inside <FeedbackProvider>');
  return value;
}
