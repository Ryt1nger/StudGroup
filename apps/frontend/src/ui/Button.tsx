import clsx from 'clsx';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import styles from './Button.module.css';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost';
  icon?: ReactNode;
  block?: boolean;
  busy?: boolean;
}

export function Button({ variant = 'primary', icon, block, busy, className, children, disabled, type = 'button', ...rest }: Props) {
  return (
    <button
      type={type}
      className={clsx(styles.button, styles[variant], block && styles.block, busy && styles.busy, className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {icon ? <span className={styles.icon}>{icon}</span> : null}
      <span>{children}</span>
    </button>
  );
}
