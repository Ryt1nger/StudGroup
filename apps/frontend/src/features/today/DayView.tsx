import type { AcademicDeadline, HomeworkSummary, LessonOccurrence } from '@studgroup/shared-types';
import { dayKey } from '../../lib/format';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { Empty } from '../../ui/Blocks';
import { DlCard, HwCard, LessonRow } from '../shared/Cards';
import { Sec } from '../shared/Sec';

export function lessonsOfDay(lessons: readonly LessonOccurrence[], key: string, timeZone: string): LessonOccurrence[] {
  return lessons
    .filter((lesson) => dayKey(lesson.starts_at, timeZone) === key && lesson.status !== 'cancelled')
    .sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at));
}

/** A control point or event belongs to a day by its deadline, or by a window that covers the day. */
function eventOnDay(item: AcademicDeadline, key: string, timeZone: string): boolean {
  if (item.deadline_at) return dayKey(item.deadline_at, timeZone) === key;
  if (!item.window_start || !item.window_end) return false;
  return dayKey(item.window_start, timeZone) <= key && dayKey(new Date(Date.parse(item.window_end) - 1), timeZone) >= key;
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
  const none = !dayLessons.length && !dayHomework.length && !dayEvents.length;
  if (none && loading) return null;
  if (none) return <Empty title="На этот день ничего нет" body="Пар, заданий и контрольных на этот день не запланировано." />;
  return (
    <>
      {dayLessons.length ? (
        <Sec id="day-lessons" title="Пары">
          {dayLessons.map((lesson) => <LessonRow key={lesson.id} lesson={lesson} timeZone={timeZone} nowMs={nowMs} />)}
        </Sec>
      ) : null}
      {dayHomework.length ? (
        <Sec id="day-homework" title="Домашние задания">
          {dayHomework.map((item) => <HwCard key={item.id} item={item} nowMs={nowMs} timeZone={timeZone} />)}
        </Sec>
      ) : null}
      {dayEvents.length ? (
        <Sec id="day-events" title="КТ и события">
          {dayEvents.map((item) => <DlCard key={item.id} item={item} timeZone={timeZone} nowMs={nowMs} />)}
        </Sec>
      ) : null}
    </>
  );
}
