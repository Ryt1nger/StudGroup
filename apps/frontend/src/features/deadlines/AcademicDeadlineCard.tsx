import type { AcademicDeadline } from '@studgroup/shared-types';
import { assets } from '../../assets';
import { Link, useLocation } from 'react-router';
import { ThemedImage } from '../../ui/ThemedImage';
import { ChevronRight, Clock } from '../../ui/icons';
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
      viewTransition
      aria-label={`Открыть событие: ${subject} — ${item.title}`}
    >
      <div className={styles.summary}>
        <ThemedImage asset={assets.tiles.exam} className={styles.icon} />
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
