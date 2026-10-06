import { Badge } from '../../ui/Badge';
import { AlertCircle, CheckCircle, Refresh } from '../../ui/icons';
import type { ScheduleBadgeView } from './viewModel';
import styles from './ScheduleBadge.module.css';

function Play() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2.5 1.5v9l8-4.5z" fill="currentColor" />
    </svg>
  );
}

export function ScheduleBadge({ badge }: { badge: ScheduleBadgeView }) {
  if (badge.tone === 'solid') {
    return (
      <span className={styles.solid}>
        {badge.kind === 'now' ? <Play /> : null}
        {badge.label}
      </span>
    );
  }
  const icon =
    badge.kind === 'confirmed' ? <CheckCircle /> : badge.kind === 'moved' ? <Refresh /> : badge.kind === 'cancelled' ? <AlertCircle /> : undefined;
  return (
    <Badge tone={badge.tone} icon={icon}>
      {badge.label}
    </Badge>
  );
}
