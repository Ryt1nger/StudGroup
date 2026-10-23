import type { HomeworkSummary } from '@studgroup/shared-types';
import clsx from 'clsx';
import { Link } from 'react-router';
import { ru } from '../../i18n/ru';
import { formatDeadline } from '../../lib/format';
import { useIsHeadman } from '../../session/SessionProvider';
import { Badge } from '../../ui/Badge';
import { subjectGlyph, subjectStyle } from '../../lib/subjectColor';
import { AlertCircle, CheckCircle, Clock, Refresh } from '../../ui/icons';
import { cardStatus, showUrgent, type CardStatus } from './cardStatus';
import styles from './HomeworkCard.module.css';
import { confirmedSubject } from '../homework/confirmedSubject';

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
  const subject = confirmedSubject(item);

  return (
    <Link
      to={`/homework/${item.id}`}
      className={clsx(styles.card, archived && styles.archived, !archived && cancelled && styles.cancelled, !archived && completed && styles.completed)}
      style={subjectStyle(subject?.name ?? item.subject.name)}
      aria-label={`${ru.card.open}: ${item.title}`}
    >
      <span className={styles.bar} aria-hidden="true" />
      <span className={styles.body}>
        <span className={styles.title}>{item.title}</span>
        {item.summary ? <span className={styles.summary}>{item.summary}</span> : null}
        <span className={styles.meta}>
          {subject ? (
            <span className={styles.subjectChip}>
              <b aria-hidden="true">{subjectGlyph(subject.name)}</b>
              {subject.name}
            </span>
          ) : null}
          <span className={styles.when}>
            <Clock className={styles.metaIcon} size={14} />
            <span>{deadline ?? ru.card.deadlineUnknown}</span>
          </span>
        </span>
        {!archived && status ? (
          <span className={styles.badges}>
            <StatusLabel status={status} />
          </span>
        ) : null}
      </span>
      {!archived && urgent ? <span className={styles.urgent} role="img" aria-label={ru.card.urgent} /> : null}
      {archived && completed ? (
        <span className={styles.archiveCompleted} role="img" aria-label={ru.card.completedByMe}>
          <CheckCircle size={18} />
        </span>
      ) : null}
    </Link>
  );
}
