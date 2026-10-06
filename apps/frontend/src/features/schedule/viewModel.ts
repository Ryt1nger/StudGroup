/**
 * PRESENTATION props for the schedule visuals — NOT API types.
 *
 * The schedule contract has not been published yet, so these components take display-ready
 * values (strings, tones, states). When Codex publishes the contract, a mapper from the
 * generated types to these props is added in one place; no API field is invented here.
 * The fields the visuals need from the contract are listed in FRONTEND_ARCHITECTURE.md §17.
 */
import type { BadgeTone } from '../../ui/Badge';

export type ScheduleBadgeKind = 'confirmed' | 'moved' | 'cancelled' | 'now' | 'group' | 'personal' | 'weekInfo';

export interface ScheduleBadgeView {
  kind: ScheduleBadgeKind;
  label: string;
  tone: BadgeTone | 'solid';
}

export type TimelineState = 'done' | 'current' | 'upcoming';

export interface ScheduleItemView {
  id: string;
  /** Start time as shown, e.g. "08:30". */
  time: string;
  title: string;
  /** e.g. "Лекция · Ауд. 105". */
  subtitle: string;
  state: TimelineState;
  cancelled?: boolean;
  badges: ScheduleBadgeView[];
  onOpen?: () => void;
}

export interface NextClassView {
  /** "Следующая пара" / "Первая пара". */
  heading: string;
  /** Right-hand chip, e.g. "Через 1 час" / "Завтра". */
  whenLabel: string;
  /** e.g. "10:30 — 12:00". */
  timeRange: string;
  subject: string;
  /** e.g. "Практика · Ауд. 320 · Иванов А. С.". */
  details: string;
  badges: ScheduleBadgeView[];
  /** e.g. "Обновлено 09:12". */
  updated?: string;
  onOpen?: () => void;
}

export interface ScheduleDayView {
  /** e.g. "Сегодня, 2 октября". */
  heading: string;
  items: ScheduleItemView[];
}

export interface ScheduleScreenView {
  /** Segmented control: shown only when the data source offers both scopes. */
  scopes?: { options: Array<{ key: string; label: string }>; active: string };
  /** Week selector label, e.g. "1 неделя · нечётная"; shown only when provided. */
  weekLabel?: string;
  days: ScheduleDayView[];
}
