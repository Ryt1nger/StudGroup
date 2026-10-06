import clsx from 'clsx';
import { NavLink, useMatch } from 'react-router';
import { assets } from '../assets';
import { ru } from '../i18n/ru';
import { useTheme } from '../telegram/TelegramProvider';
import styles from './BottomNav.module.css';

const items = [
  { key: 'home', to: '/today', label: ru.nav.today },
  { key: 'tasks', to: '/tasks', label: ru.nav.tasks },
  { key: 'schedule', to: '/schedule', label: ru.nav.schedule },
  { key: 'subjects', to: '/subjects', label: ru.nav.subjects },
] as const;

export function BottomNav() {
  const theme = useTheme();
  // A homework card is opened from Today in Slice 1, so Today stays the active section.
  const onHomework = useMatch('/homework/:id');
  const onDeadlines = useMatch('/deadlines/*');
  return (
    <nav className={styles.nav} aria-label={ru.nav.aria}>
      {items.map((item) => (
        <NavLink key={item.key} to={item.to} className={styles.link} aria-label={item.label}>
          {({ isActive }) => {
            const active = isActive || (item.key === 'home' && onHomework !== null) || (item.key === 'tasks' && onDeadlines !== null);
            return (
              <span className={clsx(styles.inner, active && styles.active)} aria-current={active ? 'page' : undefined}>
                <span className={styles.iconSlot} aria-hidden="true">
                  {(['inactive', 'active'] as const).map((state) => (
                    <img
                      key={state}
                      className={clsx(styles.icon, (active ? state === 'active' : state === 'inactive') && styles.iconVisible)}
                      src={assets.nav[item.key][state][theme]}
                      alt=""
                      width={26}
                      height={26}
                    />
                  ))}
                </span>
                <span className={styles.label}>{item.label}</span>
              </span>
            );
          }}
        </NavLink>
      ))}
    </nav>
  );
}
