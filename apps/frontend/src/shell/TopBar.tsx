import { lazy, Suspense } from 'react';
import { assets } from '../assets';
import { ThemedImage } from '../ui/ThemedImage';
import styles from './TopBar.module.css';
import { HeaderActions } from './HeaderActions';

/**
 * Only demo fixtures are lazy-loaded in development or the explicit static demo.
 * Production uses the authenticated session and persisted notification inbox.
 */
const demoBuild = import.meta.env.MODE === 'demo';
const DemoHeaderActions = import.meta.env.DEV || demoBuild ? lazy(() => import('./demo/DemoHeaderActions')) : null;

export function TopBar() {
  const demo =
    (demoBuild ||
      (import.meta.env.DEV &&
        (import.meta.env.VITE_API_MOCK === 'true' || import.meta.env.VITE_LOCAL_PREVIEW === 'true'))) &&
    DemoHeaderActions;
  return (
    <header className={styles.bar}>
      <ThemedImage asset={assets.logoSymbol} className={styles.symbol} alt="" />
      <ThemedImage asset={assets.wordmark} className={styles.wordmark} alt="StudGroup" />
      {demo ? (
        <Suspense fallback={null}>
          <DemoHeaderActions />
        </Suspense>
      ) : <HeaderActions />}
    </header>
  );
}
