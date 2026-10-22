import { Badge } from '../../ui/Badge';
import { useLessonLinkClick } from '../../ui/lessonLink';
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

function NowLink({ badge, href }: { badge: ScheduleBadgeView; href: string }) {
  const onClick = useLessonLinkClick(href);
  return (
    <a className={styles.solid} href={href} target="_blank" rel="noopener noreferrer" onClick={onClick}>
      <Play />
      {badge.label}
    </a>
  );
}

export function ScheduleBadge({ badge }: { badge: ScheduleBadgeView }) {
  if (badge.kind === 'now' && badge.href) return <NowLink badge={badge} href={badge.href} />;
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
