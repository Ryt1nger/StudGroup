import clsx from 'clsx';
import type { ReactNode } from 'react';
import styles from './Banner.module.css';

interface Props {
  tone: 'info' | 'warning' | 'danger' | 'success';
  icon?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  role?: 'status' | 'alert';
}

export function Banner({ tone, icon, children, action, role = 'status' }: Props) {
  return (
    <div className={clsx(styles.banner, styles[tone])} role={role}>
      {icon ? <span className={styles.icon}>{icon}</span> : null}
      <span className={styles.text}>{children}</span>
      {action ? <span className={styles.action}>{action}</span> : null}
    </div>
  );
}
