import clsx from 'clsx';
import type { KeyboardEvent, MouseEvent } from 'react';
import { useNavigate } from 'react-router';
import type { AcademicDeadline, HomeworkSummary, LessonOccurrence } from '@studgroup/shared-types';
import { addDays, dayKey, formatShortDate, formatTime, plural, whenLabel } from '../../lib/format';
import { subjectGlyph, subjectStyle } from '../../lib/subjectColor';
import { useReadySession } from '../../session/SessionProvider';
import { Ic } from '../../ui/Ic';
import { changedAfterCompletion, showUrgent } from '../today/cardStatus';
import { confirmedSubject } from '../homework/confirmedSubject';
import { formatEventDate } from '../deadlines/formatEventDate';

export const EVENT_KIND: Record<string, string> = {
  control_point: 'Контрольная точка',
  assessment: 'Контрольная работа',
  test: 'Тест',
  credit: 'Зачёт',
  exam: 'Экзамен',
  other_academic_deadline: 'Учебное событие',
};

export function SubjectChip({ name }: { name: string }) {
  return (
    <span className="chipx" style={subjectStyle(name)}>
      <b>{subjectGlyph(name)}</b>
      {name}
    </span>
  );
}

/** An element that behaves as a link to `to`, but may hold its own buttons (the checkbox). */
export function useRowLink(to: string) {
  const navigate = useNavigate();
  return {
    role: 'link' as const,
    tabIndex: 0,
    onClick: (event: MouseEvent) => {
      if ((event.target as HTMLElement).closest('button, a')) return;
      void navigate(to);
    },
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === 'Enter' && event.target === event.currentTarget) void navigate(to);
    },
  };
}

/** Homework list card. `archive` cards are grey and read-only. */
export function HwCard({
  item,
  nowMs,
  timeZone,
  archive = false,
  busy = false,
  onToggle,
}: {
  item: HomeworkSummary;
  nowMs: number;
  timeZone: string;
  archive?: boolean;
  busy?: boolean;
  onToggle?: (item: HomeworkSummary) => void;
}) {
  const link = useRowLink(`/homework/${item.id}`);
  const session = useReadySession();
  const canToggle = session.permissions.includes('homework.completion.write') && onToggle !== undefined;
  const done = item.my_state.completion === 'completed';
  const cancelled = item.status === 'cancelled';
  const subject = confirmedSubject(item) ?? item.subject;
  const when = whenLabel(item.deadline, nowMs, timeZone);
  const urgent = showUrgent(item) && !done && !archive;

  let tag;
  if (cancelled) tag = <span className="chipx mute">отменено</span>;
  else if (done && changedAfterCompletion(item)) tag = <span className="chipx warn"><Ic name="refresh" size={12} />Обновлено — проверьте</span>;
  else if (done) tag = <span className="chipx ok"><Ic name="check" size={12} />выполнено</span>;
  else tag = <span className="when">{when.known ? <Ic name="clock" size={13} /> : null}{when.text}</span>;

  const check = archive ? (
    <span className={clsx('cb', done ? 'on' : 'off')} aria-hidden="true">{done ? <Ic name="check" /> : null}</span>
  ) : cancelled || !canToggle ? (
    <span className="cb off" aria-hidden="true" />
  ) : (
    <button
      type="button"
      className={clsx('cb', done && 'on', busy && 'busy')}
      disabled={busy}
      aria-pressed={done}
      aria-label={done ? 'Снять отметку «Выполнено мной»' : 'Отметить «Выполнено мной»'}
      onClick={() => onToggle?.(item)}
    >
      {done ? <Ic name="check" /> : null}
    </button>
  );

  return (
    <article
      className={clsx('task', archive ? 'arch' : done && 'done', cancelled && !archive && 'cancelled')}
      style={subjectStyle(subject.name)}
      aria-label={`Открыть задание: ${item.title}`}
      {...link}
    >
      <span className="bar" />
      {check}
      <div className="tb">
        <div className="t">{item.title}</div>
        {item.summary ? <div className="s">{item.summary}</div> : null}
        <div className="m">
          <SubjectChip name={subject.name} />
          {tag}
        </div>
      </div>
      {urgent ? <span className="urg" role="img" aria-label="Срочно" /> : null}
    </article>
  );
}

/** Control point / event card. */
export function DlCard({ item, timeZone, nowMs, archive = false }: { item: AcademicDeadline; timeZone: string; nowMs: number; archive?: boolean }) {
  const link = useRowLink(`/deadlines/${item.id}`);
  const name = item.confirmed_subject?.name ?? item.subject;
  const startMs = item.deadline_at ? Date.parse(item.deadline_at) : item.window_start ? Date.parse(item.window_start) : null;
  const endMs = item.window_end ? Date.parse(item.window_end) : null;
  const inWindow = !item.deadline_at && startMs !== null && endMs !== null && !archive && startMs <= nowMs && nowMs < endMs;
  let left: number | null = null;
  if (startMs !== null && !archive && !inWindow) {
    const today = dayKey(nowMs, timeZone);
    const day = dayKey(startMs, timeZone);
    const [ty, tm, td] = today.split('-').map(Number) as [number, number, number];
    const [dy, dm, dd] = day.split('-').map(Number) as [number, number, number];
    left = Math.round((Date.UTC(dy, dm - 1, dd) - Date.UTC(ty, tm - 1, td)) / 86_400_000);
  }
  const date = item.deadline_at || item.window_start ? (item.deadline_at ? formatEventDate(item, timeZone) : `Сдача: ${formatEventDate(item, timeZone)}`) : 'Дата уточняется';
  return (
    <article className={clsx('task', archive && 'arch')} style={subjectStyle(name)} aria-label={`Открыть событие: ${item.title}`} {...link}>
      <span className="bar" />
      <span className="dlic" aria-hidden="true"><Ic name="flame" size={14} /></span>
      <div className="tb">
        <div className="t">{item.title}</div>
        <div className="s">{EVENT_KIND[item.kind]}</div>
        <div className="m">
          <SubjectChip name={name} />
          <span className="when"><Ic name="clock" size={13} />{date}</span>
          {inWindow ? <span className="chipx ok">идёт сдача</span> : null}
          {left !== null && left >= 0 ? (
            <span className="chipx ok">{left === 0 ? 'сегодня' : left === 1 ? 'завтра' : `через ${left} ${plural(left, 'день', 'дня', 'дней')}`}</span>
          ) : null}
        </div>
      </div>
    </article>
  );
}

/** A lesson as a list row (subject page, a chosen day on «Сегодня»). */
export function LessonRow({ lesson, timeZone, nowMs, showDate = false }: { lesson: LessonOccurrence; timeZone: string; nowMs: number; showDate?: boolean }) {
  const link = useRowLink(`/schedule/lesson/${encodeURIComponent(lesson.id)}`);
  const now = Date.parse(lesson.starts_at) <= nowMs && nowMs < Date.parse(lesson.ends_at);
  const place = [lesson.location, lesson.teacher].filter(Boolean).join(' · ');
  return (
    <article className={clsx('task lrow', now && 'nowlesson')} style={subjectStyle(lesson.subject)} aria-label={`Открыть пару: ${lesson.subject}`} {...link}>
      <span className="bar" />
      <div className="tm">{formatTime(lesson.starts_at, timeZone)}<small>{formatTime(lesson.ends_at, timeZone)}</small></div>
      <div className="tb">
        <div className="t">{showDate ? formatShortDate(lesson.starts_at, timeZone) : lesson.subject}</div>
        <div className="s">{showDate ? lesson.subject : place || ' '}</div>
      </div>
    </article>
  );
}

export function tomorrowKey(nowMs: number, timeZone: string): string {
  return addDays(dayKey(nowMs, timeZone), 1);
}
