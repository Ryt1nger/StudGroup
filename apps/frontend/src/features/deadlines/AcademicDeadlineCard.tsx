import type { AcademicDeadline } from '@studgroup/shared-types';
import { Link, useLocation } from 'react-router';
import { subjectStyle } from '../../lib/subjectColor';
import { ChevronRight, Clock, NavSchedule } from '../../ui/icons';
import { formatEventDate } from './formatEventDate';
import styles from './Deadlines.module.css';

export function AcademicDeadlineCard({ item, timeZone }: { item: AcademicDeadline; timeZone: string }) {
  const location = useLocation();
  const when = formatEventDate(item, timeZone);
  const subject = Object.prototype.hasOwnProperty.call(item, 'confirmed_subject')
    ? (item.confirmed_subject?.name ?? 'Без предмета')
    : item.subject;
  return (
    <Link
      to={`/deadlines/${item.id}`}
      state={{ backTo: `${location.pathname}${location.search}` }}
      className={`${styles.card} ${styles.eventLink}`}
      style={subjectStyle(subject)}
      viewTransition
      aria-label={`Открыть событие: ${subject} — ${item.title}`}
    >
      <span className={styles.bar} aria-hidden="true" />
      <div className={styles.summary}>
        <span className={styles.icon} aria-hidden="true">
          <NavSchedule size={22} />
        </span>
        <div className={styles.cardText}>
          <h3>{subject}</h3>
          <p>{item.title}</p>
          <p className={styles.when}>
            <Clock size={14} />
            <span>{when}</span>
          </p>
        </div>
        <ChevronRight className={styles.chevron} size={20} />
      </div>
    </Link>
  );
}
