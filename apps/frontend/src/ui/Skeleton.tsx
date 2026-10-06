import clsx from 'clsx';
import styles from './Skeleton.module.css';

export function Skeleton({ className, height = 16, width = '100%', radius }: { className?: string; height?: number; width?: number | string; radius?: number }) {
  return <span className={clsx(styles.skeleton, className)} style={{ height, width, borderRadius: radius }} aria-hidden="true" />;
}
