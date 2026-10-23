import clsx from 'clsx';
import { addDays } from '../../lib/format';
import styles from './WeekStrip.module.css';

const weekday = (key: string) => {
  const [y, m, d] = key.split('-').map(Number) as [number, number, number];
  const text = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', weekday: 'short' }).format(new Date(Date.UTC(y, m - 1, d, 12)));
  return text.charAt(0).toUpperCase() + text.slice(1).replace('.', '');
};

export const WEEK_DAYS = 7;

export function weekKeys(today: string): string[] {
  return Array.from({ length: WEEK_DAYS }, (_, index) => addDays(today, index));
}

/** Seven days starting today. A dot marks a day with lessons; the selected day lives in the URL. */
export function WeekStrip({
  days,
  selected,
  marked,
  onSelect,
}: {
  days: string[];
  selected: number;
  marked: ReadonlySet<string>;
  onSelect: (index: number) => void;
}) {
  return (
    <div className={styles.week} role="group" aria-label="День">
      {days.map((key, index) => (
        <button
          key={key}
          type="button"
          className={clsx(styles.day)}
          aria-pressed={index === selected}
          aria-label={`${weekday(key)}, ${Number(key.slice(8))}`}
          onClick={() => onSelect(index)}
        >
          <span>{weekday(key)}</span>
          <b>{Number(key.slice(8))}</b>
          <i className={marked.has(key) ? undefined : styles.empty} />
        </button>
      ))}
    </div>
  );
}
