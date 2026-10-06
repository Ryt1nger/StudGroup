import clsx from 'clsx';
import type { ReactNode } from 'react';
import styles from './Badge.module.css';

export type BadgeTone = 'success' | 'info' | 'warning' | 'danger' | 'primary' | 'neutral';

interface Props {
  tone: BadgeTone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}

/** Status pill. Always carries text (and usually an icon), never colour alone. */
export function Badge({ tone, icon, children, className, title }: Props) {
  return (
    <span className={clsx(styles.badge, styles[tone], className)} title={title}>
      {icon ? <span className={styles.icon}>{icon}</span> : null}
      <span>{children}</span>
    </span>
  );
}
