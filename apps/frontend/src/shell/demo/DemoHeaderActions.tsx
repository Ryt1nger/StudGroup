import clsx from 'clsx';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useSession } from '../../session/SessionProvider';
import { Badge } from '../../ui/Badge';
import { MOCK_ADAPTER_MARKER } from '../../telegram/mockAdapter';
import { useTelegram, useTheme } from '../../telegram/TelegramProvider';
import { Bell, Close, Moon, Sun } from '../../ui/icons';
import { markAllDemoRead, markDemoRead, useDemoNotifications } from './demoStore';
import styles from './DemoHeaderActions.module.css';

function initialsOf(name: string | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return parts
    .slice(0, 2)
    .map((p) => p.charAt(0).toUpperCase())
    .join('');
}

/** Must match the exit animation duration in DemoHeaderActions.module.css. */
const EXIT_MS = 180;

function Panel({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [leaving, setLeaving] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  // Fade/slide out first, then unmount; with reduced motion there is no animation to wait for.
  const requestClose = useCallback(() => {
    if (timer.current !== undefined) return;
    if (typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      onClose();
      return;
    }
    setLeaving(true);
    timer.current = window.setTimeout(onClose, EXIT_MS);
  }, [onClose]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') requestClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [requestClose]);
  return (
    <div className={clsx(styles.overlay, leaving && styles.leaving)}>
      <button type="button" className={styles.backdrop} aria-label="Закрыть" tabIndex={-1} onClick={requestClose} />
      <section className={styles.panel} role="dialog" aria-modal="true" aria-label={title}>
        <header className={styles.panelHead}>
          <h2 className={styles.panelTitle}>{title}</h2>
          <Badge tone="primary">Демо</Badge>
          <button ref={closeRef} type="button" className={styles.close} onClick={requestClose} aria-label="Закрыть">
            <Close size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

type SchemeSetter = { setScheme(next: 'light' | 'dark'): void };

/** Demo theme switch: drives the dev Telegram mock, exactly like Telegram changing its colour scheme. */
function ThemeSwitch() {
  const scheme = useTheme();
  const { adapter } = useTelegram();
  const hook = (globalThis as Record<string, unknown>)[MOCK_ADAPTER_MARKER] as SchemeSetter | undefined;
  const setScheme = adapter?.setColorScheme ? (next: 'light' | 'dark') => adapter.setColorScheme!(next) : hook?.setScheme;
  if (!setScheme) return null;
  const dark = scheme === 'dark';
  return (
    <button
      type="button"
      className={styles.themeSwitch}
      role="switch"
      aria-checked={dark}
      onClick={() => setScheme(dark ? 'light' : 'dark')}
    >
      <span className={styles.themeIcon}>{dark ? <Moon size={20} /> : <Sun size={20} />}</span>
      <span className={styles.themeLabel}>Тёмная тема</span>
      <span className={styles.track} aria-hidden="true">
        <span className={styles.thumb} />
      </span>
    </button>
  );
}

/**
 * DEV preview header actions. Not part of production builds; notifications are demo data.
 * Initials and group come from the active session (mock or isolated real-data preview).
 */
export default function DemoHeaderActions() {
  const [open, setOpen] = useState<'bell' | 'profile' | null>(null);
  const bellRef = useRef<HTMLButtonElement>(null);
  const avatarRef = useRef<HTMLButtonElement>(null);
  const notifications = useDemoNotifications();
  const { state } = useSession();
  const user = state.status === 'ready' ? state.session.user : undefined;
  const group = state.status === 'ready' ? state.session.group : undefined;
  const initials = initialsOf(user?.display_name);
  const hasUnread = notifications.some((n) => n.unread);

  const close = () => {
    const was = open;
    setOpen(null);
    queueMicrotask(() => (was === 'bell' ? bellRef : avatarRef).current?.focus());
  };

  return (
    <div className={styles.actions}>
      <button
        ref={bellRef}
        type="button"
        className={styles.bell}
        onClick={() => setOpen('bell')}
        aria-label={hasUnread ? 'Уведомления (демо), есть непрочитанные' : 'Уведомления (демо)'}
        aria-haspopup="dialog"
      >
        <Bell size={24} />
        {hasUnread ? <span className={styles.dot} data-testid="demo-unread-dot" /> : null}
      </button>
      <button
        ref={avatarRef}
        type="button"
        className={styles.avatar}
        onClick={() => setOpen('profile')}
        aria-label={`Профиль (демо): ${user?.display_name ?? 'тестовый пользователь'}`}
        aria-haspopup="dialog"
      >
        {initials}
      </button>

      {open === 'bell' ? (
        <Panel title="Уведомления" onClose={close}>
          <p className={styles.note}>Демонстрационные данные: реальные уведомления пока недоступны.</p>
          <ul className={styles.list}>
            {notifications.map((n) => (
              <li key={n.id}>
                <button type="button" className={styles.item} onClick={() => markDemoRead(n.id)} aria-label={`${n.title}${n.unread ? ', непрочитано' : ''}`}>
                  <span className={n.unread ? styles.itemDot : styles.itemDotRead} aria-hidden="true" />
                  <span className={styles.itemBody}>
                    <span className={styles.itemTitle}>{n.title}</span>
                    <span className={styles.itemText}>{n.body}</span>
                    <span className={styles.itemWhen}>{n.when}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className={styles.link} onClick={markAllDemoRead} disabled={!hasUnread}>
            Отметить все прочитанными
          </button>
        </Panel>
      ) : null}

      {open === 'profile' ? (
        <Panel title="Профиль" onClose={close}>
          <div className={styles.profile}>
            <span className={styles.avatarLarge} aria-hidden="true">
              {initials}
            </span>
            <span className={styles.profileText}>
              <span className={styles.profileName}>{user?.display_name ?? 'Тестовый пользователь'}</span>
              {group ? <span className={styles.itemText}>{group.name}</span> : null}
            </span>
          </div>
          <ThemeSwitch />
          <p className={styles.note}>Демонстрационная панель: остальные настройки и действия профиля пока недоступны.</p>
        </Panel>
      ) : null}
    </div>
  );
}
