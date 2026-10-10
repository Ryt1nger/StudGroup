import { useQueryClient } from '@tanstack/react-query';
import type { LessonOccurrence, ScheduleResponse } from '@studgroup/shared-types';
import clsx from 'clsx';
import { useCallback } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { ru } from '../../i18n/ru';
import { formatDateTile, formatLongDay, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { useBackButton } from '../../shell/useBackButton';
import { Badge } from '../../ui/Badge';
import { Button } from '../../ui/Button';
import { ChevronLeft, Clock } from '../../ui/icons';
import { StateView } from '../../ui/StateView';
import styles from './LessonScreen.module.css';

interface LessonState {
  lesson?: LessonOccurrence;
  timeZone?: string;
}

/** The lesson is taken from navigation state, else from any cached GET /schedule response. */
function useLesson(
  lessonId: string,
  state: LessonState | null,
): { lesson: LessonOccurrence | null; timeZone: string | null } {
  const queryClient = useQueryClient();
  if (state?.lesson && state.lesson.id === lessonId)
    return { lesson: state.lesson, timeZone: state.timeZone ?? null };
  for (const [, data] of queryClient.getQueriesData<ScheduleResponse>({ queryKey: ['schedule'] })) {
    const found = data?.lessons.find((l) => l.id === lessonId);
    if (found && data) return { lesson: found, timeZone: data.group_timezone };
  }
  return { lesson: null, timeZone: null };
}

function relativeLabel(
  startMs: number,
  endMs: number,
  nowMs: number,
): { text: string; kind: 'now' | 'done' | 'upcoming' } {
  if (nowMs >= endMs) return { text: ru.schedule.lesson.statusDone, kind: 'done' };
  if (nowMs >= startMs) return { text: ru.schedule.lesson.statusNow, kind: 'now' };
  const minutes = Math.max(1, Math.round((startMs - nowMs) / 60_000));
  if (minutes < 60) return { text: ru.schedule.lesson.inMinutes(minutes), kind: 'upcoming' };
  if (minutes < 24 * 60)
    return { text: ru.schedule.lesson.inHours(Math.floor(minutes / 60), minutes % 60), kind: 'upcoming' };
  return { text: ru.schedule.lesson.statusUpcoming, kind: 'upcoming' };
}

/**
 * `/schedule/lesson/:lessonId`. Shows ONLY fields of the generated LessonOccurrence. Lesson type,
 * materials, notes and related homework need a contract addition (see DESIGN_HANDOFF §14).
 */
export function LessonScreen() {
  const { lessonId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const sessionTz = useGroupTimeZone();
  const { lesson, timeZone: foundTz } = useLesson(lessonId, location.state as LessonState | null);
  const timeZone = foundTz ?? sessionTz;
  // Re-render tick; the session clock (server time) wins over the device clock.
  const deviceNow = useServerNow(undefined, 0);
  const sessionNow = useSessionServerNow();
  const nowMs = sessionNow() ?? deviceNow;

  const goBack = useCallback(() => {
    if (window.history.state && typeof window.history.state.idx === 'number' && window.history.state.idx > 0)
      void navigate(-1);
    else void navigate('/schedule', { replace: true });
  }, [navigate]);
  const nativeBack = useBackButton(goBack);

  const header = (
    <header className={styles.header}>
      {!nativeBack ? (
        <button type="button" className={styles.back} onClick={goBack} aria-label={ru.detail.back}>
          <ChevronLeft size={24} />
        </button>
      ) : (
        <span className={styles.backSpacer} />
      )}
      <h1 className={styles.headerTitle}>{lesson ? lesson.subject : ru.schedule.lesson.notFoundTitle}</h1>
      <span className={styles.backSpacer} />
    </header>
  );

  if (!lesson) {
    return (
      <div className={styles.page}>
        {header}
        <StateView
          title={ru.schedule.lesson.notFoundTitle}
          body={ru.schedule.lesson.notFoundBody}
          action={
            <Button onClick={() => void navigate('/schedule', { replace: true })}>
              {ru.schedule.lesson.back}
            </Button>
          }
        />
      </div>
    );
  }

  const startMs = Date.parse(lesson.starts_at);
  const endMs = Date.parse(lesson.ends_at);
  const rel = relativeLabel(startMs, endMs, nowMs);
  const tile = formatDateTile(lesson.starts_at, timeZone);
  const range = `${formatTime(lesson.starts_at, timeZone)} — ${formatTime(lesson.ends_at, timeZone)}`;
  const minutes = Math.max(0, Math.round((endMs - startMs) / 60_000));
  const rows: Array<{ label: string; value: string }> = [
    { label: ru.schedule.lesson.date, value: formatLongDay(lesson.starts_at, timeZone) },
    { label: ru.schedule.lesson.time, value: range },
    ...(minutes > 0
      ? [{ label: ru.schedule.lesson.duration, value: ru.schedule.lesson.durationValue(minutes) }]
      : []),
    ...(lesson.teacher ? [{ label: ru.schedule.lesson.teacher, value: lesson.teacher }] : []),
    ...(lesson.location ? [{ label: ru.schedule.lesson.location, value: lesson.location }] : []),
    { label: ru.schedule.lesson.status, value: ru.schedule.lesson.statusScheduled },
  ];

  return (
    <div className={styles.page}>
      {header}
      <div className={styles.stack}>
        <section className={styles.hero}>
          <div className={clsx(styles.dateTile, rel.kind === 'now' && styles.dateTileNow)}>
            <span className={styles.dateDay}>{tile.day}</span>
            <span className={styles.dateMonth}>{tile.month}</span>
          </div>
          <div className={styles.heroText}>
            <h2 className={styles.heroTitle}>{lesson.subject}</h2>
            <p className={styles.heroTime}>
              <Clock size={14} />
              <span>{range}</span>
            </p>
            <div className={styles.heroBadges}>
              <Badge tone={rel.kind === 'now' ? 'success' : rel.kind === 'done' ? 'neutral' : 'info'}>
                {rel.text}
              </Badge>
            </div>
          </div>
        </section>
        <section className={styles.card} aria-labelledby="lesson-details">
          <h2 id="lesson-details" className={styles.cardTitle}>
            {ru.schedule.lesson.detailsTitle}
          </h2>
          <dl className={styles.rows}>
            {rows.map((row) => (
              <div key={row.label} className={styles.row}>
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </div>
  );
}
