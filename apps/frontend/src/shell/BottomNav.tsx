import clsx from 'clsx';
import type { CSSProperties } from 'react';
import { matchPath, NavLink, useLocation } from 'react-router';
import { ru } from '../i18n/ru';
import { NavSchedule, NavSubjects, NavTasks, NavToday } from '../ui/icons';
import styles from './BottomNav.module.css';

const items = [
  { key: 'home', to: '/today', label: ru.nav.today, Icon: NavToday },
  { key: 'tasks', to: '/tasks', label: ru.nav.tasks, Icon: NavTasks },
  { key: 'schedule', to: '/schedule', label: ru.nav.schedule, Icon: NavSchedule },
  { key: 'subjects', to: '/subjects', label: ru.nav.subjects, Icon: NavSubjects },
] as const;

/** Index of the section that owns the current route (a card opened from Today keeps Today active). */
function activeIndex(pathname: string): number {
  if (matchPath({ path: '/homework/*', end: false }, pathname)) return 0;
  if (matchPath({ path: '/deadlines/*', end: false }, pathname)) return 1;
  return items.findIndex((item) => matchPath({ path: item.to, end: false }, pathname));
}

export function BottomNav() {
  const { pathname } = useLocation();
  const index = activeIndex(pathname);
  return (
    <nav className={styles.nav} aria-label={ru.nav.aria} style={{ '--sg-nav-index': Math.max(index, 0) } as CSSProperties}>
      <span className={clsx(styles.indicator, index < 0 && styles.indicatorHidden)} aria-hidden="true" />
      {items.map((item, i) => {
        const active = i === index;
        return (
          <NavLink key={item.key} to={item.to} className={styles.link} aria-label={item.label}>
            <span className={clsx(styles.inner, active && styles.active)} aria-current={active ? 'page' : undefined}>
              <span className={styles.iconSlot} aria-hidden="true">
                <item.Icon size={24} />
              </span>
              <span className={styles.label}>{item.label}</span>
            </span>
          </NavLink>
        );
      })}
    </nav>
  );
}
