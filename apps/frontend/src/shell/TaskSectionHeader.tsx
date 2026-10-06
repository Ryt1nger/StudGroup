import { useLocation, useNavigate } from 'react-router';
import { TopBar } from './TopBar';
import styles from '../features/today/TodayScreen.module.css';

/** Shared shell header: buttons stay mounted while the task/deadline content changes. */
export function TaskSectionHeader() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return <>
    <TopBar />
    <div className={styles.page}>
      <div className={styles.daySwitch} role="group" aria-label="Задания и дедлайны">
        {([{ path: '/tasks', label: 'Задания' }, { path: '/deadlines', label: 'Дедлайны' }] as const).map(({ path, label }) => {
          const active = pathname === path;
          return <div key={path} className={styles.title} role={active ? 'heading' : undefined} aria-level={active ? 1 : undefined}>
            <button type="button" aria-pressed={active} className={active ? styles.activeDay : styles.inactiveDay} onClick={() => { if (!active) void navigate(path); }}>{label}</button>
          </div>;
        })}
      </div>
    </div>
  </>;
}
