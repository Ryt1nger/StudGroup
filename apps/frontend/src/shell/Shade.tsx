import clsx from 'clsx';
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { listNotifications, readAllNotifications, type NotificationInbox } from '@studgroup/shared-types';
import { unwrap } from '../api/errors';
import { plural } from '../lib/format';
import { useSession } from '../session/SessionProvider';
import { Banner, Skeleton, Spinner } from '../ui/Blocks';
import { Ic } from '../ui/Ic';
import type { IconName } from '../ui/iconData';
import { Float } from './AppRoot';

type Item = NotificationInbox['items'][number];

const ICON: Record<Item['entity_type'], { icon: IconName; kind: string }> = {
  homework: { icon: 'tasks', kind: 'new' },
  deadline: { icon: 'clock', kind: 'deadline' },
  schedule: { icon: 'cal', kind: 'schedule' },
};

function ago(iso: string, now = Date.now()): string {
  const minutes = Math.max(1, Math.round((now - Date.parse(iso)) / 60_000));
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.round(hours / 24);
  return days === 1 ? 'вчера' : `${days} ${plural(days, 'день', 'дня', 'дней')} назад`;
}

const reduceMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** One notification. A horizontal swipe or the small cross removes it from the list. */
function Row({ item, onOpen, onDismiss }: { item: Item; onOpen: () => void; onDismiss: () => void }) {
  const rowRef = useRef<HTMLDivElement>(null);
  const liRef = useRef<HTMLLIElement>(null);
  const swipe = useRef<{ x: number; y: number; dx: number; moved: boolean; id: number; t: number } | null>(null);
  const guard = useRef(0);
  const meta = ICON[item.entity_type];

  const leave = (direction: number) => {
    const row = rowRef.current;
    const li = liRef.current;
    if (!row || !li || li.dataset.leaving) return;
    li.dataset.leaving = '1';
    if (reduceMotion()) return onDismiss();
    row.style.transition = 'transform .22s cubic-bezier(.4,0,.8,.6), opacity .22s';
    row.style.transform = `translateX(${direction * 115}%)`;
    row.style.opacity = '0';
    window.setTimeout(() => {
      li.style.height = `${li.offsetHeight}px`;
      li.style.overflow = 'hidden';
      void li.offsetHeight;
      li.style.transition = 'height .2s ease, border-color .2s';
      li.style.height = '0px';
      li.style.borderTopColor = 'transparent';
      window.setTimeout(onDismiss, 210);
    }, 200);
  };

  const down = (event: ReactPointerEvent) => {
    if ((event.target as HTMLElement).closest('.ndis') || event.button > 0) return;
    swipe.current = { x: event.clientX, y: event.clientY, dx: 0, moved: false, id: event.pointerId, t: performance.now() };
  };
  const move = (event: ReactPointerEvent) => {
    const s = swipe.current;
    const row = rowRef.current;
    if (!s || !row || event.pointerId !== s.id) return;
    const dx = event.clientX - s.x;
    const dy = event.clientY - s.y;
    if (!s.moved) {
      if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) {
        swipe.current = null;
        return;
      }
      if (Math.abs(dx) < 8) return;
      s.moved = true;
      row.classList.add('dragging');
      try {
        row.setPointerCapture(event.pointerId);
      } catch {
        /* pointer already released */
      }
    }
    s.dx = dx;
    row.style.transform = `translateX(${dx}px)`;
    row.style.opacity = String(1 - Math.min(Math.abs(dx) / 280, 0.65));
  };
  const up = (event: ReactPointerEvent) => {
    const s = swipe.current;
    const row = rowRef.current;
    if (!s || !row || event.pointerId !== s.id) return;
    swipe.current = null;
    if (!s.moved) return;
    guard.current = performance.now() + 250; // the click that follows a drag must not open the notification
    row.classList.remove('dragging');
    const speed = Math.abs(s.dx) / Math.max(1, performance.now() - s.t);
    if (Math.abs(s.dx) > 96 || (speed > 0.55 && Math.abs(s.dx) > 40)) leave(Math.sign(s.dx) || 1);
    else {
      row.style.transition = 'transform .2s, opacity .2s';
      row.style.transform = '';
      row.style.opacity = '';
    }
  };

  return (
    <li ref={liRef}>
      <div ref={rowRef} className="nrow" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
        <button type="button" className="nitem" onClick={() => performance.now() >= guard.current && onOpen()}>
          <span className={clsx('nic', meta.kind)}><Ic name={meta.icon} size={20} /></span>
          <span className="nb">
            <b>{item.title}</b>
            <span>{item.body}</span>
            <time dateTime={item.created_at}>{ago(item.created_at)}</time>
          </span>
        </button>
        <button type="button" className="ndis" aria-label="Убрать уведомление" onClick={() => leave(1)}><Ic name="close" size={14} /></button>
      </div>
    </li>
  );
}

function Shade({ onClose }: { onClose: () => void }) {
  const { state } = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const shadeRef = useRef<HTMLElement>(null);
  const pull = useRef<{ y: number; dy: number; id: number; moved: boolean } | null>(null);
  const guard = useRef(0);

  const inbox = useQuery({
    queryKey: ['notifications'],
    queryFn: () => unwrap(listNotifications()),
    enabled: state.status === 'ready',
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  const clear = useMutation({
    mutationFn: () => unwrap(readAllNotifications()),
    onSuccess: (data) => queryClient.setQueryData(['notifications'], data),
  });

  const close = () => {
    if (leaving) return;
    setLeaving(true);
    window.setTimeout(onClose, 200);
  };
  useEffect(() => {
    shadeRef.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        const nodes = [...(shadeRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]') ?? [])];
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaving]);

  const items = useMemo(
    () => (inbox.data?.items ?? []).filter((item) => !item.read && !dismissed.has(item.id)),
    [inbox.data, dismissed],
  );
  const dismiss = (id: string) => setDismissed((current) => new Set(current).add(id));
  const today0 = new Date().setHours(0, 0, 0, 0);
  const groups = [
    ['Сегодня', items.filter((item) => Date.parse(item.created_at) >= today0)],
    ['Ранее', items.filter((item) => Date.parse(item.created_at) < today0)],
  ] as const;

  const open = (item: Item) => {
    dismiss(item.id);
    onClose();
    void navigate(item.entity_type === 'schedule' ? '/schedule' : `/${item.entity_type === 'homework' ? 'homework' : 'deadlines'}/${item.entity_id}`);
  };

  const handleDown = (event: ReactPointerEvent) => {
    pull.current = { y: event.clientY, dy: 0, id: event.pointerId, moved: false };
  };
  const handleMove = (event: ReactPointerEvent) => {
    const p = pull.current;
    const shade = shadeRef.current;
    if (!p || !shade || event.pointerId !== p.id) return;
    p.dy = Math.min(0, event.clientY - p.y);
    if (p.dy < -6) p.moved = true;
    if (p.moved) {
      shade.style.animation = 'none';
      shade.style.transition = 'none';
      shade.style.transform = `translateY(${p.dy}px)`;
    }
  };
  const handleUp = (event: ReactPointerEvent) => {
    const p = pull.current;
    const shade = shadeRef.current;
    if (!p || !shade || event.pointerId !== p.id) return;
    pull.current = null;
    if (!p.moved) return;
    guard.current = performance.now() + 250;
    if (p.dy < -70) close();
    else {
      shade.style.transition = 'transform .2s';
      shade.style.transform = '';
    }
  };

  const unread = items.length;
  const sub = unread ? `Новых: ${unread}` : 'Пока ничего нового';
  return (
    <div className={clsx('shade-back', leaving && 'leaving')} onClick={(event) => event.target === event.currentTarget && close()}>
      <section ref={shadeRef} className="shade" role="dialog" aria-modal="true" aria-label="Уведомления" tabIndex={-1}>
        <header className="sd-head">
          <div>
            <h2>Уведомления</h2>
            <span className="sd-sub">{sub}</span>
          </div>
          <div className="sd-act">
            {unread > 0 && !inbox.isPending ? (
              <button type="button" className={clsx('pillb', clear.isPending && 'busy')} disabled={clear.isPending} onClick={() => clear.mutate()}>
                {clear.isPending ? <><Spinner />Очищаем…</> : <><Ic name="trash" size={15} />Очистить все</>}
              </button>
            ) : null}
          </div>
        </header>
        <div className="sd-body">
          {clear.isError ? <Banner tone="danger" icon="alert">Не удалось очистить уведомления. Попробуйте ещё раз.</Banner> : null}
          {inbox.isPending ? <div className="nsk"><Skeleton count={4} label="Загрузка уведомлений" /></div> : null}
          {inbox.isError ? (
            <div className="nempty" role="alert">
              <div className="glyph"><Ic name="alert" size={26} /></div>
              <b>Не удалось загрузить</b>
              <p>Проверьте соединение и попробуйте ещё раз.</p>
              <button type="button" className="btn on sbtn" onClick={() => void inbox.refetch()}><Ic name="refresh" size={18} />Повторить</button>
            </div>
          ) : null}
          {inbox.data && items.length === 0 ? (
            <div className="nempty">
              <div className="glyph"><Ic name="bell" size={26} /></div>
              <b>Пока уведомлений нет</b>
              <p>Новые задания, изменения сроков и напоминания появятся здесь. Просмотренные исчезают.</p>
            </div>
          ) : null}
          {groups.map(([title, list]) =>
            list.length ? (
              <div key={title}>
                <div className="grph">{title}</div>
                <ul className="nlist">
                  {list.map((item) => (
                    <Row key={item.id} item={item} onOpen={() => open(item)} onDismiss={() => dismiss(item.id)} />
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </div>
        <button
          type="button"
          className="sd-handle"
          aria-label="Закрыть уведомления"
          onPointerDown={handleDown}
          onPointerMove={handleMove}
          onPointerUp={handleUp}
          onPointerCancel={handleUp}
          onClick={() => performance.now() >= guard.current && close()}
        >
          <i />
        </button>
      </section>
    </div>
  );
}

export function BellButton() {
  const { state } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const inbox = useQuery({
    queryKey: ['notifications'],
    queryFn: () => unwrap(listNotifications()),
    enabled: state.status === 'ready',
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  const unread = inbox.data?.unread_count ?? 0;
  return (
    <>
      <button ref={ref} type="button" className="icon-btn" aria-label={`Уведомления${unread ? ', есть непрочитанные' : ''}`} onClick={() => setOpen(true)}>
        <Ic name="bell" />
        {unread ? <i className="pip" /> : null}
      </button>
      {open ? (
        <Float>
          <Shade
            onClose={() => {
              setOpen(false);
              ref.current?.focus();
            }}
          />
        </Float>
      ) : null}
    </>
  );
}
