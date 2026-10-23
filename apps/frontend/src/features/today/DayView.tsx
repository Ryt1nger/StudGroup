import { Link } from 'react-router';
import type { AcademicDeadline, HomeworkSummary, LessonOccurrence } from '@studgroup/shared-types';
import { dayKey, formatTime } from '../../lib/format';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { subjectStyle } from '../../lib/subjectColor';
import { StateView } from '../../ui/StateView';
import { AcademicDeadlineCard } from '../deadlines/AcademicDeadlineCard';
import { HomeworkCard } from './HomeworkCard';
import styles from './TodayScreen.module.css';

export function lessonsOfDay(lessons: readonly LessonOccurrence[], key: string, timeZone: string): LessonOccurrence[] {
  return lessons.filter((lesson) => dayKey(lesson.starts_at, timeZone) === key && lesson.status !== 'cancelled');
}

/** A control point or event belongs to a day by its deadline, or by a window that covers the day. */
function eventOnDay(item: AcademicDeadline, key: string, timeZone: string): boolean {
  if (item.deadline_at) return dayKey(item.deadline_at, timeZone) === key;
  if (!item.window_start || !item.window_end) return false;
  return dayKey(item.window_start, timeZone) <= key && dayKey(item.window_end, timeZone) >= key;
}

function homeworkOnDay(item: HomeworkSummary, key: string, nowMs: number, timeZone: string): boolean {
  if (item.status === 'cancelled' || item.deadline.state !== 'known' || !item.deadline.at) return false;
  return dayKey(item.deadline.at, timeZone) === key && !isHomeworkArchived(item, nowMs, timeZone);
}

/** One chosen day other than today: that day's lessons, homework due that day and control points / events. */
export function DayView({
  dayKeyValue,
  timeZone,
  nowMs,
  lessons,
  homework,
  events,
  loading,
}: {
  dayKeyValue: string;
  timeZone: string;
  nowMs: number;
  lessons: readonly LessonOccurrence[];
  homework: readonly HomeworkSummary[];
  events: readonly AcademicDeadline[];
  loading: boolean;
}) {
  const dayLessons = lessonsOfDay(lessons, dayKeyValue, timeZone);
  const dayHomework = homework.filter((item) => homeworkOnDay(item, dayKeyValue, nowMs, timeZone));
  const dayEvents = events.filter((item) => eventOnDay(item, dayKeyValue, timeZone));
  if (loading && !dayLessons.length && !dayHomework.length && !dayEvents.length) return null;
  if (!dayLessons.length && !dayHomework.length && !dayEvents.length) {
    return <StateView title="На этот день ничего нет" body="Пар, заданий и контрольных на этот день не запланировано." />;
  }
  return (
    <>
      {dayLessons.length ? (
        <section className={styles.section} aria-labelledby="day-lessons">
          <h2 id="day-lessons" className={styles.sectionTitle}>Пары</h2>
          <div className={styles.list}>
            {dayLessons.map((lesson) => (
              <Link
                key={lesson.id}
                className={styles.lessonRow}
                style={subjectStyle(lesson.subject)}
                to={`/subjects/lessons/${encodeURIComponent(lesson.id)}`}
                viewTransition
              >
                <span className={styles.lessonBar} aria-hidden="true" />
                <span className={styles.lessonTime}>{formatTime(lesson.starts_at, timeZone)}<small>{formatTime(lesson.ends_at, timeZone)}</small></span>
                <span className={styles.lessonBody}>
                  <b>{lesson.subject}</b>
                  <small>{[lesson.location, lesson.teacher].filter(Boolean).join(' · ')}</small>
                </span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}
      {dayHomework.length ? (
        <section className={styles.section} aria-labelledby="day-homework">
          <h2 id="day-homework" className={styles.sectionTitle}>Домашние задания</h2>
          <div className={styles.list}>
            {dayHomework.map((item) => (
              <HomeworkCard key={item.id} item={item} nowMs={nowMs} timeZone={timeZone} />
            ))}
          </div>
        </section>
      ) : null}
      {dayEvents.length ? (
        <section className={styles.section} aria-labelledby="day-events">
          <h2 id="day-events" className={styles.sectionTitle}>КТ и события</h2>
          <div className={styles.list}>
            {dayEvents.map((item) => (
              <AcademicDeadlineCard key={item.id} item={item} timeZone={timeZone} />
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}
