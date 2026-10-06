import type { HomeworkSummary } from '@studgroup/shared-types';
import clsx from 'clsx';
import { Link } from 'react-router';
import { ru } from '../../i18n/ru';
import { formatDeadline } from '../../lib/format';
import { useIsHeadman } from '../../session/SessionProvider';
import { Badge } from '../../ui/Badge';
import { homeworkTileAsset } from '../../ui/entityIcons';
import { ThemedImage } from '../../ui/ThemedImage';
import { AlertCircle, CheckCircle, ChevronRight, Clock, Refresh } from '../../ui/icons';
import { cardStatus, showUrgent, type CardStatus } from './cardStatus';
import { urgencyBadge } from './homeworkBadges';
import styles from './HomeworkCard.module.css';

interface Props {
  item: HomeworkSummary;
  nowMs: number;
  timeZone: string;
  archived?: boolean;
}

function StatusLabel({ status }: { status: CardStatus }) {
  switch (status) {
    case 'cancelled':
      return <Badge tone="neutral">{ru.card.cancelled}</Badge>;
    case 'updated':
      return (
        <Badge tone="warning" icon={<Refresh />}>
          {ru.card.updatedCheck}
        </Badge>
      );
    case 'clarify':
      return (
        <Badge tone="warning" icon={<AlertCircle />}>
          {ru.card.needsClarification}
        </Badge>
      );
    case 'completed':
      return (
        <Badge tone="success" icon={<CheckCircle />}>
          {ru.card.completedByMe}
        </Badge>
      );
  }
}

/**
 * List card: title, subject and deadline are always shown. At most one primary status label
 * (priority in cardStatus) plus optional «Срочно» (urgency only, never for cancelled).
 * Source, import, verification and processing state live on the detail screen.
 */
export function HomeworkCard({ item, nowMs, timeZone, archived = false }: Props) {
  const deadline = formatDeadline(item.deadline, nowMs, timeZone);
  const isHeadman = useIsHeadman();
  const status = cardStatus(item, isHeadman);
  const urgent = showUrgent(item);
  const cancelled = status === 'cancelled';
  const completed = item.my_state.completion === 'completed';

  return (
    <Link
      to={`/homework/${item.id}`}
      className={clsx(styles.card, archived && styles.archived, !archived && cancelled && styles.cancelled)}
      aria-label={`${ru.card.open}: ${item.title}`}
    >
      <ThemedImage
        asset={homeworkTileAsset({ urgent: !archived && urgent, completed: !archived && completed, cancelled })}
        className={clsx(styles.tile, !archived && urgent && !completed && styles.tileUrgent)}
      />
      <span className={styles.body}>
        <span className={styles.title}>{item.title}</span>
        <span className={styles.subject}>{item.subject.name}</span>
        <span className={styles.meta}>
          <Clock className={styles.metaIcon} size={14} />
          <span>{deadline ?? ru.card.deadlineUnknown}</span>
        </span>
        {!archived && (status || urgent) ? (
          <span className={styles.badges}>
            {status ? <StatusLabel status={status} /> : null}
            {urgent ? urgencyBadge(item.urgency) : null}
          </span>
        ) : null}
      </span>
      {archived && completed ? (
        <span className={styles.archiveCompleted} role="img" aria-label={ru.card.completedByMe}>
          <CheckCircle size={18} />
        </span>
      ) : null}
      <ChevronRight className={styles.chevron} size={20} />
    </Link>
  );
}
