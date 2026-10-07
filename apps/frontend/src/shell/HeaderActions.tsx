import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { listNotifications, readAllNotifications } from '@studgroup/shared-types';
import { unwrap } from '../api/errors';
import { useSession } from '../session/SessionProvider';
import { useTheme } from '../telegram/TelegramProvider';
import { Bell, Close } from '../ui/icons';
import styles from './demo/DemoHeaderActions.module.css';

export function HeaderActions() {
  const { state } = useSession();
  const scheme = useTheme();
  const [open, setOpen] = useState<'profile' | 'bell' | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const bellRef = useRef<HTMLButtonElement>(null);
  const profileRef = useRef<HTMLButtonElement>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const inbox = useQuery({ queryKey: ['notifications'], queryFn: () => unwrap(listNotifications()),
    enabled: state.status === 'ready', refetchInterval: 60000, refetchIntervalInBackground: false });
  const read = useMutation({ mutationFn: () => unwrap(readAllNotifications()),
    onSuccess: (data) => queryClient.setQueryData(['notifications'], data) });
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(null);
      if (event.key === 'Tab') {
        const panel = closeRef.current?.closest('section');
        const buttons = panel?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
        if (!buttons?.length) return;
        const first = buttons[0]; const last = buttons[buttons.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); (open === 'bell' ? bellRef : profileRef).current?.focus(); };
  }, [open]);
  if (state.status !== 'ready') return null;
  const { user, group, membership } = state.session;
  const initials = user.display_name.trim().split(/\s+/).slice(0, 2).map((name) => name[0]).join('').toUpperCase();
  const roles = { student: 'Участник', headman: 'Староста', deputy: 'Помощник старосты' };
  return <div className={styles.actions}>
    <button ref={bellRef} className={styles.bell} aria-label={`Уведомления${inbox.data?.unread_count ? ', есть непрочитанные' : ''}`} onClick={() => setOpen('bell')}>
      <Bell size={26} />{inbox.data?.unread_count ? <span className={styles.dot} /> : null}
    </button>
    <button ref={profileRef} className={styles.avatar} aria-label={`Профиль: ${user.display_name}`} onClick={() => setOpen('profile')}>{initials || '?'}</button>
    {open ? <div className={styles.overlay}>
      <button className={styles.backdrop} tabIndex={-1} aria-label="Закрыть" onClick={() => setOpen(null)} />
      <section className={styles.panel} role="dialog" aria-modal="true" aria-label={open === 'profile' ? 'Профиль' : 'Уведомления'}>
        <header className={styles.panelHead}><h2 className={styles.panelTitle}>{open === 'profile' ? 'Профиль' : 'Уведомления'}</h2>
          <button ref={closeRef} className={styles.close} aria-label="Закрыть" onClick={() => setOpen(null)}><Close size={20} /></button></header>
        {open === 'profile' ? <>
          <div className={styles.profile}><span className={styles.avatarLarge}>{initials}</span><div className={styles.profileText}>
            <span className={styles.profileName}>{user.display_name}</span>{user.username ? <span>@{user.username}</span> : null}
            <span>{membership ? roles[membership.role] : ''}</span></div></div>
          <p>{group?.name}</p><p className={styles.note}>Telegram ID: {user.telegram_user_id}</p>
          <p className={styles.note}>Тема: {scheme === 'dark' ? 'тёмная' : 'светлая'} · из настроек Telegram</p>
        </> : <>
          {inbox.isPending ? <p role="status">Загрузка уведомлений…</p> : null}
          {inbox.isError ? <button className={styles.link} onClick={() => void inbox.refetch()}>Не удалось загрузить. Повторить</button> : null}
          {inbox.data?.items.length === 0 ? <p className={styles.note}>Пока уведомлений нет</p> : null}
          <ul className={styles.list}>{inbox.data?.items.map((item) => <li key={item.id}><button className={styles.item} onClick={() => {
            setOpen(null); navigate(`/${item.entity_type === 'homework' ? 'homework' : 'deadlines'}/${item.entity_id}`);
          }}><span className={item.read ? styles.itemDotRead : styles.itemDot} /><span className={styles.itemBody}>
            <span className={styles.itemTitle}>{item.title}</span><span className={styles.itemText}>{item.body}</span>
          </span></button></li>)}</ul>
          <button className={styles.link} disabled={!inbox.data?.unread_count || read.isPending} onClick={() => read.mutate()}>Отметить все прочитанными</button>
          {read.isError ? <p role="alert">Не удалось сохранить прочтение. Попробуйте ещё раз.</p> : null}
        </>}
      </section>
    </div> : null}
  </div>;
}
