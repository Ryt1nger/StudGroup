import { Link } from 'react-router';
import type { NextLesson } from '@studgroup/shared-types';
import { dayKey, addDays, formatTime } from '../../lib/format';
import { subjectStyle } from '../../lib/subjectColor';
import { safeLessonUrl } from '../../lib/lessonLink';
import { Ic } from '../../ui/Ic';

/** The next lesson: a large card at the top of «Сегодня». The backend picks the lesson and its state. */
export function Hero({ next, nowMs, timeZone }: { next: NextLesson; nowMs: number; timeZone: string }) {
  const { lesson } = next;
  const going = next.state === 'current';
  const today = dayKey(nowMs, timeZone);
  const day = dayKey(lesson.starts_at, timeZone);
  const label = going ? 'Идёт сейчас' : day === today ? 'Следующая пара' : day === addDays(today, 1) ? 'Завтра, первая пара' : 'Ближайшая пара';
  const online = safeLessonUrl(lesson.online_url) !== null;
  const place = online ? 'онлайн' : lesson.location;
  return (
    <Link
      className="hero"
      style={subjectStyle(lesson.subject)}
      to={`/schedule/lesson/${encodeURIComponent(lesson.id)}`}
      state={{ lesson, timeZone }}
      aria-label={`${label}: ${lesson.subject}`}
    >
      <div className="eyebrow"><i className={going ? 'live' : undefined} />{label}</div>
      <div className="hrow">
        <div className="time">{formatTime(lesson.starts_at, timeZone)}</div>
        <div className="hinfo">
          <b>{lesson.subject}</b>
          {place ? <span>{place}</span> : null}
          {lesson.teacher ? <span>{lesson.teacher}</span> : null}
        </div>
      </div>
      {online ? <span className="cta"><Ic name="video" size={16} />Подключиться</span> : null}
    </Link>
  );
}
