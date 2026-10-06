import { Outlet, useLocation } from 'react-router';
import { Banner } from '../ui/Banner';
import { WifiOff } from '../ui/icons';
import { ru } from '../i18n/ru';
import { useOnline } from '../lib/hooks';
import { BottomNav } from './BottomNav';
import { TaskSectionHeader } from './TaskSectionHeader';
import styles from './AppShell.module.css';

export function AppShell() {
  const online = useOnline();
  const { pathname } = useLocation();
  return (
    <div className={styles.shell}>
      {!online ? (
        <div className={styles.offline}>
          <Banner tone="warning" icon={<WifiOff />} role="alert">
            {ru.states.offlineTitle}. {ru.states.offlineBody}
          </Banner>
        </div>
      ) : null}
      <main className={styles.main}>
        {import.meta.env.DEV && import.meta.env.VITE_LOCAL_PREVIEW === 'true' ? (
          <Banner tone="info">Реальные данные · тестовый импорт. Задания требуют проверки.</Banner>
        ) : null}
        {pathname === '/tasks' || pathname === '/deadlines' ? <TaskSectionHeader /> : null}
        <div key={pathname} className={styles.screen}>
          <Outlet />
        </div>
      </main>
      <BottomNav />
    </div>
  );
}
