import { useRef, useState } from 'react';
import { addDays, weekRangeForDayKey } from '../../lib/format';

const SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

function parts(key: string): { weekday: string; day: number } {
  const [y, m, d] = key.split('-').map(Number) as [number, number, number];
  return { weekday: SHORT[new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay()]!, day: d };
}

/** Fixed Monday–Sunday calendar week containing the supplied day. */
export function weekKeys(day: string): string[] {
  const monday = weekRangeForDayKey(day).start;
  return Array.from({ length: 7 }, (_, index) => addDays(monday, index));
}

/** Seven days from today. A dot marks a day that has lessons; the chosen day is an `aria-pressed` button. */
export function WeekStrip({
  days,
  selected,
  marked,
  onSelect,
  onWeekChange,
}: {
  days: readonly string[];
  selected: number;
  marked: ReadonlySet<string>;
  onSelect: (index: number) => void;
  onWeekChange?: (direction: -1 | 1) => void;
}) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const suppressClick = useRef(false);
  const [direction, setDirection] = useState(0);
  const first = days[0];
  return (
    <div className="week" role="group" aria-label="День" data-week={first} data-direction={direction} tabIndex={0}
      onKeyDown={(event) => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); onWeekChange?.(event.key === 'ArrowRight' ? 1 : -1); } }}
      onTouchStart={(event) => { start.current = { x: event.touches[0]!.clientX, y: event.touches[0]!.clientY }; }}
      onTouchEnd={(event) => {
        if (!start.current || !onWeekChange) return;
        const dx = event.changedTouches[0]!.clientX - start.current.x;
        const dy = event.changedTouches[0]!.clientY - start.current.y;
        start.current = null;
        if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.3) return;
        suppressClick.current = true;
        window.setTimeout(() => { suppressClick.current = false; }, 350);
        const next = dx < 0 ? 1 : -1;
        setDirection(next);
        onWeekChange(next);
      }}
    >
      {days.map((key, index) => {
        const { weekday, day } = parts(key);
        return (
          <button key={key} type="button" className="wd" aria-pressed={index === selected} aria-label={`${weekday}, ${day}`} onClick={() => { if (!suppressClick.current) onSelect(index); }}>
            <span>{weekday}</span>
            <b>{day}</b>
            <i style={marked.has(key) ? undefined : { opacity: 0 }} />
          </button>
        );
      })}
    </div>
  );
}
