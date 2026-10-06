import { assets } from '../../assets';
import { ThemedImage } from '../../ui/ThemedImage';
import { Clock, ChevronRight } from '../../ui/icons';
import { ScheduleBadge } from './ScheduleBadge';
import type { NextClassView } from './viewModel';
import styles from './NextClassCard.module.css';

function CalendarGlyph() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="5" width="16" height="15" rx="3" />
      <path d="M8 3v4M16 3v4M4 10h16M8.5 14h7" />
    </svg>
  );
}

/** «Следующая пара» block for «Сегодня». Presentational: values come from props only. */
export function NextClassCard({ view }: { view: NextClassView }) {
  const Root = view.onOpen ? 'button' : 'div';
  return (
    <Root className={styles.card} type={view.onOpen ? 'button' : undefined} onClick={view.onOpen} aria-label={view.onOpen ? `${view.heading}: ${view.subject}` : undefined}>
      <ThemedImage asset={assets.nextCard} className={styles.decor} />
      <span className={styles.top}>
        <span className={styles.glyph}>
          <CalendarGlyph />
        </span>
        <span className={styles.heading}>{view.heading}</span>
        <span className={styles.when}>{view.whenLabel}</span>
      </span>
      <span className={styles.row}>
        <span className={styles.main}>
          <span className={styles.time}>{view.timeRange}</span>
          <span className={styles.subject}>{view.subject}</span>
          <span className={styles.details}>{view.details}</span>
        </span>
        {view.onOpen ? (
          <span className={styles.go} aria-hidden="true">
            <ChevronRight size={20} />
          </span>
        ) : null}
      </span>
      {view.badges.length > 0 ? (
        <span className={styles.badges}>
          {view.badges.map((b) => (
            <ScheduleBadge key={b.kind + b.label} badge={b} />
          ))}
        </span>
      ) : null}
      {view.updated ? (
        <span className={styles.updated}>
          <Clock size={14} />
          {view.updated}
        </span>
      ) : null}
    </Root>
  );
}
