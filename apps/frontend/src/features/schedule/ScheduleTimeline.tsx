import clsx from 'clsx';
import { ChevronRight, Check } from '../../ui/icons';
import { ScheduleBadge } from './ScheduleBadge';
import type { ScheduleDayView, ScheduleItemView } from './viewModel';
import styles from './ScheduleTimeline.module.css';

function SubjectTile() {
  // Subject icon/colour are not in the contract yet; a neutral book tile is used until they are.
  return (
    <span className={styles.tile} aria-hidden="true">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 23H20" />
      </svg>
    </span>
  );
}

function Row({ item }: { item: ScheduleItemView }) {
  const Root = item.onOpen ? 'button' : 'div';
  return (
    <li className={clsx(styles.item, styles[item.state])}>
      <span className={styles.time}>{item.time}</span>
      <span className={styles.node} aria-hidden="true">
        {item.state === 'done' ? <Check size={12} strokeWidth={3} /> : null}
      </span>
      <Root className={clsx(styles.card, item.state === 'current' && styles.cardCurrent, item.cancelled && styles.cancelled)} type={item.onOpen ? 'button' : undefined} onClick={item.onOpen}>
        <SubjectTile />
        <span className={styles.body}>
          <span className={styles.title}>{item.title}</span>
          <span className={styles.subtitle}>{item.subtitle}</span>
          {item.badges.length > 0 ? (
            <span className={styles.badges}>
              {item.badges.map((b) => (
                <ScheduleBadge key={b.kind + b.label} badge={b} />
              ))}
            </span>
          ) : null}
        </span>
        {item.onOpen ? <ChevronRight className={styles.chevron} size={20} /> : null}
      </Root>
    </li>
  );
}

export function ScheduleTimeline({ day }: { day: ScheduleDayView }) {
  return (
    <section className={styles.day} aria-label={day.heading}>
      <h2 className={styles.heading}>{day.heading}</h2>
      <ol className={styles.list}>
        {day.items.map((item) => (
          <Row key={item.id} item={item} />
        ))}
      </ol>
    </section>
  );
}
