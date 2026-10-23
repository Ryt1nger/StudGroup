import { addDays } from '../../lib/format';

const SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

function parts(key: string): { weekday: string; day: number } {
  const [y, m, d] = key.split('-').map(Number) as [number, number, number];
  return { weekday: SHORT[new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay()]!, day: d };
}

/** Seven consecutive day keys starting at `today`. */
export function weekKeys(today: string): string[] {
  return Array.from({ length: 7 }, (_, index) => addDays(today, index));
}

/** Seven days from today. A dot marks a day that has lessons; the chosen day is an `aria-pressed` button. */
export function WeekStrip({
  days,
  selected,
  marked,
  onSelect,
}: {
  days: readonly string[];
  selected: number;
  marked: ReadonlySet<string>;
  onSelect: (index: number) => void;
}) {
  return (
    <div className="week" role="group" aria-label="День">
      {days.map((key, index) => {
        const { weekday, day } = parts(key);
        return (
          <button key={key} type="button" className="wd" aria-pressed={index === selected} aria-label={`${weekday}, ${day}`} onClick={() => onSelect(index)}>
            <span>{weekday}</span>
            <b>{day}</b>
            <i style={marked.has(key) ? undefined : { opacity: 0 }} />
          </button>
        );
      })}
    </div>
  );
}
