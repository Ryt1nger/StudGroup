import type { CSSProperties, ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router';

export const CALENDAR_PX_PER_HOUR = 64;
export function calendarCardMinHeight(durationMinutes: number) {
  return Math.max(54, durationMinutes * CALENDAR_PX_PER_HOUR / 60 - 4);
}

/** The exact same card surface and text hierarchy for lessons and group events. */
export function CalendarCard({ title, when, meta, badge, accessory, className = '', style, ...link }: LinkProps & {
  title: string;
  when: string;
  meta?: string | null;
  badge?: ReactNode;
  accessory?: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <Link {...link} className={`ev ${className}`} style={style}>
      <span className="bar" aria-hidden="true" />
      {badge}
      <h3>{title}{accessory}</h3>
      <span>{when}</span>
      <span className="dim">{meta || '\u00a0'}</span>
    </Link>
  );
}
