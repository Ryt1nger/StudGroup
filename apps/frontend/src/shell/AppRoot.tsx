import { createContext, useContext, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** The `.app` element: floating bars and the FAB live directly under it, outside the animated view. */
export const AppRootContext = createContext<HTMLElement | null>(null);

export function Float({ children }: { children: ReactNode }) {
  const root = useContext(AppRootContext);
  return root ? createPortal(children, root) : null;
}
