import type { ReactNode } from 'react';
import { ru } from '../i18n/ru';
import styles from './StateView.module.css';

interface Props {
  title: string;
  body?: string;
  illustration?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  correlationId?: string | null;
  role?: 'status' | 'alert';
}

/** Full-area state for empty, error, offline, access and prepared-section cases. */
export function StateView({ title, body, illustration, icon, action, correlationId, role }: Props) {
  return (
    <section className={styles.view} role={role} aria-live={role === 'alert' ? 'assertive' : undefined}>
      {illustration ? <div className={styles.illustration}>{illustration}</div> : null}
      {icon ? <div className={styles.icon}>{icon}</div> : null}
      <h2 className={styles.title}>{title}</h2>
      {body ? <p className={styles.body}>{body}</p> : null}
      {action ? <div className={styles.action}>{action}</div> : null}
      {correlationId ? (
        <p className={styles.code}>
          {ru.states.codeLabel}: <code>{correlationId}</code>
        </p>
      ) : null}
    </section>
  );
}
