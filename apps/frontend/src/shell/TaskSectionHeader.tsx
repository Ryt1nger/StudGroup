import { useLocation, useNavigate } from 'react-router';
import { ru } from '../i18n/ru';
import { useHasPermission } from '../session/SessionProvider';
import { Plus } from '../ui/icons';
import { TopBar } from './TopBar';
import styles from '../features/today/TodayScreen.module.css';
import own from './TaskSectionHeader.module.css';

/** Shared shell header: buttons stay mounted while the task/deadline content changes. */
export function TaskSectionHeader() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const canCreate = useHasPermission('homework.create');
  return <>
    <TopBar />
    <div className={styles.page}>
      <div className={own.row}>
        <div className={styles.daySwitch} role="group" aria-label="Задания и дедлайны">
          {([{ path: '/tasks', label: 'Задания' }, { path: '/deadlines', label: 'Дедлайны' }] as const).map(({ path, label }) => {
            const active = pathname === path;
            return <div key={path} className={styles.title} role={active ? 'heading' : undefined} aria-level={active ? 1 : undefined}>
              <button type="button" aria-pressed={active} className={active ? styles.activeDay : styles.inactiveDay} onClick={() => { if (!active) void navigate(path); }}>{label}</button>
            </div>;
          })}
        </div>
        {canCreate ? (
          <button
            type="button"
            className={own.add}
            aria-label={ru.edit.create.add}
            onClick={() => void navigate(pathname === '/deadlines' ? '/cards/new?type=event' : '/cards/new')}
          >
            <Plus size={24} strokeWidth={2.5} />
          </button>
        ) : null}
      </div>
    </div>
  </>;
}
