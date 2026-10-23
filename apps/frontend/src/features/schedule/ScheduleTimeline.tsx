import clsx from 'clsx';
import { ChevronRight, Check } from '../../ui/icons';
import { ScheduleBadge } from './ScheduleBadge';
import type { ScheduleDayView, ScheduleItemView } from './viewModel';
import styles from './ScheduleTimeline.module.css';
import { dayAnchorId, lessonAnchorId } from './scheduleAnchor';
import { JoinLessonLink } from '../../ui/JoinLessonLink';
import { subjectStyle } from '../../lib/subjectColor';

function Row({ item }: { item: ScheduleItemView }) {
  const Root = item.onOpen ? 'button' : 'div';
  // The «Сейчас» badge of an online lesson is a link, and a link cannot sit inside the card's
  // button: such a card becomes a div with a full-size button laid under its content.
  const joinsNow = item.state === 'current' && !item.cancelled && item.badges.some((b) => b.href);
  const content = (
    <>
      <span className={styles.bar} aria-hidden="true" />
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
    </>
  );
  const cardClass = clsx(styles.card, item.state === 'current' && styles.cardCurrent, item.cancelled && styles.cancelled);
  return (
    <li id={lessonAnchorId(item.id)} className={clsx(styles.item, styles[item.state])} style={subjectStyle(item.title)}>
      <span className={styles.time}>{item.time}</span>
      <span className={styles.node} aria-hidden="true">
        {item.state === 'done' ? <Check size={12} strokeWidth={3} /> : null}
      </span>
      {joinsNow ? (
        <div className={clsx(cardClass, styles.cardStretch)}>
          {item.onOpen ? <button type="button" className={styles.hit} aria-label={item.title} onClick={item.onOpen} /> : null}
          {content}
        </div>
      ) : (
        <Root className={cardClass} type={item.onOpen ? 'button' : undefined} onClick={item.onOpen}>
          {content}
        </Root>
      )}
      {item.onlineUrl && !item.cancelled && item.state !== 'done' && !joinsNow ? <div className={styles.join}><JoinLessonLink url={item.onlineUrl} /></div> : null}
    </li>
  );
}

export function ScheduleTimeline({ day }: { day: ScheduleDayView }) {
  return (
    <section id={day.date ? dayAnchorId(day.date) : undefined} className={styles.day} aria-label={day.heading}>
      <h2 className={styles.heading}>{day.heading}</h2>
      {day.items.length === 0 ? <p>Сегодня пар нет</p> : null}
      <ol className={styles.list}>
        {day.items.map((item) => (
          <Row key={item.id} item={item} />
        ))}
      </ol>
    </section>
  );
}
