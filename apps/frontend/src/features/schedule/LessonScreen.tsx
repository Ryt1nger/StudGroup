import { useQueryClient } from '@tanstack/react-query';
import type { LessonOccurrence, ScheduleResponse, TodayResponse } from '@studgroup/shared-types';
import { Link, useLocation, useParams } from 'react-router';
import { useLessonSubjectQuery, useScheduleQuery } from '../../api/queries';
import { queryKeys } from '../../api/keys';
import { ru } from '../../i18n/ru';
import { dayKey, formatLongDay, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { safeLessonUrl, useLessonLinkClick } from '../../lib/lessonLink';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { subjectStyle } from '../../lib/subjectColor';
import { SubjectMark } from '../../ui/SubjectMark';
import { useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Float } from '../../shell/AppRoot';
import { Empty } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';
import { HwCard } from '../shared/Cards';
import { Sec } from '../shared/Sec';
import { useToggleDone } from '../shared/useToggleDone';
import { weekKeys } from '../shared/WeekStrip';

interface LessonState {
  lesson?: LessonOccurrence;
  timeZone?: string;
}

/** The lesson comes from navigation state, else from any cached schedule or Today response, else from the week's schedule. */
function useLesson(lessonId: string, state: LessonState | null, fallbackTz: string, nowMs: number) {
  const queryClient = useQueryClient();
  let found: { lesson: LessonOccurrence; timeZone: string } | null = null;
  if (state?.lesson && state.lesson.id === lessonId) found = { lesson: state.lesson, timeZone: state.timeZone ?? fallbackTz };
  if (!found) {
    for (const [, data] of queryClient.getQueriesData<ScheduleResponse>({ queryKey: ['schedule'] })) {
      const lesson = data?.lessons.find((l) => l.id === lessonId);
      if (lesson && data) {
        found = { lesson, timeZone: data.group_timezone };
        break;
      }
    }
  }
  if (!found) {
    for (const [, data] of queryClient.getQueriesData<TodayResponse>({ queryKey: queryKeys.today })) {
      if (data?.next_lesson?.lesson.id === lessonId) {
        found = { lesson: data.next_lesson.lesson, timeZone: data.group_timezone };
        break;
      }
    }
  }
  const days = weekKeys(dayKey(nowMs, fallbackTz));
  const week = useScheduleQuery(days[0]!, days[6]!, found === null);
  if (!found && week.data) {
    const lesson = week.data.lessons.find((l) => l.id === lessonId);
    if (lesson) found = { lesson, timeZone: week.data.group_timezone };
  }
  return { found, loading: found === null && week.isPending };
}

function relative(startMs: number, endMs: number, nowMs: number): { text: string; kind: 'now' | 'done' | 'soon' } {
  if (nowMs >= endMs) return { text: ru.schedule.lesson.statusDone, kind: 'done' };
  if (nowMs >= startMs) return { text: ru.schedule.lesson.statusNow, kind: 'now' };
  const minutes = Math.max(1, Math.round((startMs - nowMs) / 60_000));
  if (minutes < 60) return { text: ru.schedule.lesson.inMinutes(minutes), kind: 'soon' };
  if (minutes < 24 * 60) return { text: ru.schedule.lesson.inHours(Math.floor(minutes / 60), minutes % 60), kind: 'soon' };
  return { text: ru.schedule.lesson.statusUpcoming, kind: 'soon' };
}

function JoinBar({ url }: { url: string }) {
  const open = useLessonLinkClick(url);
  return (
    <Float>
      <div className="cta-bar">
        <a className="btn" href={url} target="_blank" rel="noopener noreferrer" onClick={open}>
          <Ic name="video" size={18} />
          Подключиться к паре
        </a>
      </div>
    </Float>
  );
}

/** `/schedule/lesson/:lessonId`: only fields of the lesson occurrence, plus the subject's open homework. */
export function LessonScreen() {
  const { lessonId = '' } = useParams();
  const location = useLocation();
  const sessionTz = useGroupTimeZone();
  const deviceNow = useServerNow(undefined, 0);
  const sessionNow = useSessionServerNow();
  const nowMs = sessionNow() ?? deviceNow;
  const { found, loading } = useLesson(lessonId, location.state as LessonState | null, sessionTz, nowMs);
  const subject = useLessonSubjectQuery(found ? lessonId : '');
  const { toggle, busy } = useToggleDone();

  if (!found) {
    return (
      <>
        <BackTop fallback="/schedule" />
        {loading ? null : (
          <>
            <Empty title={ru.schedule.lesson.notFoundTitle} body={ru.schedule.lesson.notFoundBody} />
            <div className="morew"><Link className="btn on sbtn" to="/schedule" replace>{ru.schedule.lesson.back}</Link></div>
          </>
        )}
      </>
    );
  }

  const { lesson, timeZone } = found;
  const startMs = Date.parse(lesson.starts_at);
  const endMs = Date.parse(lesson.ends_at);
  const rel = relative(startMs, endMs, nowMs);
  const range = `${formatTime(lesson.starts_at, timeZone)} — ${formatTime(lesson.ends_at, timeZone)}`;
  const minutes = Math.max(0, Math.round((endMs - startMs) / 60_000));
  const online = safeLessonUrl(lesson.online_url);
  const canJoin = online !== null && lesson.status !== 'cancelled' && endMs > nowMs;
  const homework = (subject.data?.homework ?? []).filter(
    (item) => item.my_state.completion !== 'completed' && !isHomeworkArchived(item, nowMs, timeZone),
  );
  const rows: Array<[string, string]> = [
    [ru.schedule.lesson.date, formatLongDay(lesson.starts_at, timeZone)],
    [ru.schedule.lesson.time, range],
    ...(minutes > 0 ? [[ru.schedule.lesson.duration, ru.schedule.lesson.durationValue(minutes)] as [string, string]] : []),
    ...(lesson.teacher ? [[ru.schedule.lesson.teacher, lesson.teacher] as [string, string]] : []),
    ...(lesson.location || online ? [[ru.schedule.lesson.location, online ? 'Онлайн' : lesson.location!] as [string, string]] : []),
    [ru.schedule.lesson.status, lesson.status === 'cancelled' ? 'Отменена' : ru.schedule.lesson.statusScheduled],
  ];

  return (
    <>
      <BackTop fallback="/schedule" />
      <div className="d-head" style={subjectStyle(lesson.subject)}>
        <span className="chipx"><b><SubjectMark name={lesson.subject} /></b>Пара</span>
        <h1>{lesson.subject}</h1>
        <div className="m">
          <span className={`chipx ${rel.kind === 'now' ? 'live' : rel.kind === 'done' ? 'mute' : 'ok'}`}>{rel.text}</span>
          <span className="when"><Ic name="clock" size={14} />{range}</span>
        </div>
      </div>
      <div className="block">
        <h3>{ru.schedule.lesson.detailsTitle}</h3>
        {rows.map(([label, value]) => (
          <div key={label} className="kv"><span>{label}</span><b>{value}</b></div>
        ))}
      </div>
      <div className="grp">
        <Link className="srow" to={`/subjects/lessons/${encodeURIComponent(lesson.id)}`}>
          <span className="ri"><Ic name="book" size={18} /></span>
          <div><div className="st">Всё по предмету</div><div className="ss">{lesson.subject}</div></div>
          <span className="sv"><Ic name="chev" /></span>
        </Link>
      </div>
      {homework.length ? (
        <Sec id="lesson-homework" title="Задания по предмету">
          {homework.map((item) => (
            <HwCard key={item.id} item={item} nowMs={nowMs} timeZone={timeZone} busy={busy.has(item.id)} onToggle={(card) => void toggle(card)} />
          ))}
        </Sec>
      ) : null}
      {canJoin ? (
        <>
          <div className="cta-space" />
          <JoinBar url={online} />
        </>
      ) : null}
    </>
  );
}
