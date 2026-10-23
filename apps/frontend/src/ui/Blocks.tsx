import clsx from 'clsx';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Ic } from './Ic';
import type { IconName } from './iconData';

export function Spinner() {
  return <i className="spinner" aria-hidden="true" />;
}

export function Skeleton({ count = 3, label }: { count?: number; label: string }) {
  return (
    <div role="status" aria-label={label}>
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="sk" />
      ))}
    </div>
  );
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <div className="glyph" aria-hidden="true">✦</div>
      <b role="heading" aria-level={2}>{title}</b>
      <p>{body}</p>
    </div>
  );
}

export type BannerTone = 'info' | 'warning' | 'danger';

export function Banner({ tone, icon, children, action }: { tone: BannerTone; icon: IconName; children: ReactNode; action?: ReactNode }) {
  return (
    <div className={clsx('bnr', tone)} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="bi"><Ic name={icon} size={18} /></span>
      <span className="bt">{children}</span>
      {action}
    </div>
  );
}

/** A full-width state: access gates, request errors, empty sections that need an explanation. */
export function StateView({
  icon,
  title,
  body,
  code,
  action,
  role = 'alert',
}: {
  icon: IconName;
  title: string;
  body: string;
  code?: string | null;
  action?: ReactNode;
  role?: 'alert' | 'status';
}) {
  return (
    <div className="state" role={role}>
      <div className="sic"><Ic name={icon} size={28} /></div>
      <b role="heading" aria-level={2}>{title}</b>
      <p>{body}</p>
      {code ? <p className="code2">Код обращения: {code}</p> : null}
      {action}
    </div>
  );
}

export function StateButton({ children, icon, onClick, busy, primary }: { children: ReactNode; icon?: IconName; onClick: () => void; busy?: boolean; primary?: boolean }) {
  return (
    <button type="button" className={clsx('btn sbtn', primary && 'on', busy && 'busy')} disabled={busy} onClick={onClick}>
      {icon ? <Ic name={icon} size={18} /> : null}
      {children}
    </button>
  );
}

/** Confirmation dialog. Escape and the backdrop dismiss it; focus moves into it while open. */
export function ConfirmModal({
  icon,
  title,
  body,
  confirm,
  cancel,
  onConfirm,
  onCancel,
}: {
  icon: IconName;
  title: string;
  body: string;
  confirm: string;
  cancel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    ref.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [onCancel]);
  return (
    <div className="modal-back" onClick={(event) => event.target === event.currentTarget && onCancel()}>
      <div ref={ref} className="modal" role="alertdialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="mic"><Ic name={icon} size={24} /></div>
        <h3 id={titleId}>{title}</h3>
        <p>{body}</p>
        <div className="mbtns">
          <button type="button" className="danger" onClick={onConfirm}>{confirm}</button>
          <button type="button" onClick={onCancel}>{cancel}</button>
        </div>
      </div>
    </div>
  );
}
