import { lazy, Suspense } from 'react';
import { useSession } from '../session/SessionProvider';
import styles from './TopBar.module.css';
import { HeaderActions } from './HeaderActions';

/**
 * Only demo fixtures are lazy-loaded in development or the explicit static demo.
 * Production uses the authenticated session and persisted notification inbox.
 */
const demoBuild = import.meta.env.MODE === 'demo';
const DemoHeaderActions = import.meta.env.DEV || demoBuild ? lazy(() => import('./demo/DemoHeaderActions')) : null;

export function TopBar() {
  const { state } = useSession();
  const group = state.status === 'ready' ? state.session.group?.name : null;
  const demo =
    (demoBuild ||
      (import.meta.env.DEV &&
        (import.meta.env.VITE_API_MOCK === 'true' || import.meta.env.VITE_LOCAL_PREVIEW === 'true'))) &&
    DemoHeaderActions;
  return (
    <header className={styles.bar}>
      <span className={styles.group}>
        <i className={styles.dot} aria-hidden="true" />
        {group ?? 'StudGroup'}
      </span>
      {demo ? (
        <Suspense fallback={null}>
          <DemoHeaderActions />
        </Suspense>
      ) : <HeaderActions />}
    </header>
  );
}
