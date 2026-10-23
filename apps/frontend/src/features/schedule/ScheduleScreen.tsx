import { useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import type { LessonOccurrence } from '@studgroup/shared-types';
import { useScheduleQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { dayKey, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { safeLessonUrl, useLessonLinkClick } from '../../lib/lessonLink';
import { subjectStyle } from '../../lib/subjectColor';
import { useSession, useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { SectionTop } from '../../shell/TopBar';
import { Banner, Empty, Skeleton, StateView } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';
import { RequestError } from '../../ui/RequestError';
import { WeekStrip, weekKeys } from '../shared/WeekStrip';
import { lessonsOfDay } from '../today/DayView';

const HR0 = 8;
const HR1 = 21;
const PX = 64;
const WEEKDAYS = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const minutesOf = (hhmm: string) => {
  const [h, m] = hhmm.split(':').map(Number) as [number, number];
  return (h - HR0) * 60 + m;
};

/** «Сейчас» of an online lesson: a link inside the card link, so a span with the link role. */
function JoinBadge({ url }: { url: string }) {
  const open = useLessonLinkClick(url);
  return (
    <span
      className="nowbadge"
      role="link"
      tabIndex={0}
      aria-label="Подключиться к занятию"
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        open(event);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          event.stopPropagation();
          window.open(url, '_blank', 'noopener,noreferrer');
        }
      }}
    >
      <Ic name="play" size={11} />
      {ru.schedule.now}
    </span>
  );
}

function Event({ lesson, timeZone, nowMs, isToday }: { lesson: LessonOccurrence; timeZone: string; nowMs: number; isToday: boolean }) {
  const start = Date.parse(lesson.starts_at);
  const end = Date.parse(lesson.ends_at);
  const now = isToday && start <= nowMs && nowMs < end;
  const from = formatTime(lesson.starts_at, timeZone);
  const to = formatTime(lesson.ends_at, timeZone);
  const top = (minutesOf(from) * PX) / 60;
  const height = Math.max(54, ((minutesOf(to) - minutesOf(from)) * PX) / 60 - 4);
  const online = safeLessonUrl(lesson.online_url);
  const joinNow = now && online !== null;
  return (
    <Link
      className={`ev${now ? ' now' : ''}${joinNow ? ' hasbadge' : ''}`}
      data-start={start}
      data-end={end}
      style={{ ...subjectStyle(lesson.subject), top, height }}
      to={`/schedule/lesson/${encodeURIComponent(lesson.id)}`}
      state={{ lesson, timeZone }}
    >
      <span className="bar" />
      {joinNow ? <JoinBadge url={online} /> : null}
      <b>
        {lesson.subject}
        {online ? <i className="vid"><Ic name="video" size={14} /></i> : null}
      </b>
      <span>{from} – {to}</span>
      <span className="dim">{[online ? 'Онлайн' : lesson.location, lesson.teacher].filter(Boolean).join(' · ')}</span>
    </Link>
  );
}

/** Scrolls so the lesson in progress (else the next one, else the last) sits near the top. */
function anchor(scroll: HTMLElement, nowMs: number) {
  const events = [...scroll.querySelectorAll<HTMLElement>('.ev')];
  if (!events.length) return;
  const target =
    events.find((e) => Number(e.dataset.start) <= nowMs && nowMs < Number(e.dataset.end)) ??
    events.find((e) => Number(e.dataset.start) > nowMs) ??
    events[events.length - 1]!;
  const y = target.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop - 150;
  scroll.scrollTop = Math.max(0, y);
}

export function ScheduleScreen() {
  const timeZone = useGroupTimeZone();
  const { state } = useSession();
  const allowed = state.status === 'ready' && state.session.permissions.includes('schedule.read');
  const serverNow = useSessionServerNow();
  const [startMs] = useState(() => serverNow() ?? Date.now());
  const today = dayKey(startMs, timeZone);
  const days = weekKeys(today);
  const [selected, setSelected] = useState(0);
  const query = useScheduleQuery(days[0]!, days[6]!, allowed);
  const data = query.data;
  const nowMs = useServerNow(data?.generated_at, query.dataUpdatedAt);
  const ref = useRef<HTMLDivElement>(null);
  const anchored = useRef('');
  const key = days[selected]!;
  const lessons = data ? lessonsOfDay(data.lessons, key, timeZone) : [];
  const marked = new Set(data ? days.filter((d) => lessonsOfDay(data.lessons, d, timeZone).length > 0) : []);

  useLayoutEffect(() => {
    const id = `${key}:${lessons.length}`;
    if (!data || anchored.current === id || !lessons.length) return;
    anchored.current = id;
    const scroll = ref.current?.closest<HTMLElement>('.scroll');
    if (scroll) anchor(scroll, nowMs);
  }, [data, key, lessons.length, nowMs]);

  if (!allowed) {
    return (
      <>
        <SectionTop title="Расписание" />
        <StateView icon="lock" title={ru.schedule.forbiddenTitle} body={ru.schedule.forbiddenBody} />
      </>
    );
  }

  const [y, m, d] = key.split('-').map(Number) as [number, number, number];
  const label = `${WEEKDAYS[new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay()]}, ${d} ${MONTHS[m - 1]}`;
  const clock = formatTime(new Date(nowMs).toISOString(), timeZone);
  const nowM = minutesOf(clock);
  const nowOn = selected === 0 && nowM > 0 && nowM < (HR1 - HR0) * 60;
  const nowY = (nowM * PX) / 60;
  const hours = Array.from({ length: HR1 - HR0 + 1 }, (_, i) => {
    const top = i * PX;
    const covered = nowOn && Math.abs(nowY - top) < 19;
    return (
      <div key={i} className="hr" style={{ top }}>
        <span style={covered ? { visibility: 'hidden' } : undefined}>{String(HR0 + i).padStart(2, '0')}:00</span>
      </div>
    );
  });

  return (
    <>
      <SectionTop title="Расписание" />
      <h1 className="sr">{ru.nav.schedule}</h1>
      <div className="datel">{label}</div>
      <WeekStrip days={days} selected={selected} marked={marked} onSelect={(i) => { setSelected(i); anchored.current = ''; }} />
      {data?.week_state === 'needs_clarification' ? (
        <Banner tone="warning" icon="clock"><strong>{ru.schedule.clarifyTitle}.</strong> {ru.schedule.clarifyBody}</Banner>
      ) : null}
      {query.isError && data ? <Banner tone="warning" icon="clock">{ru.freshness.stale(formatTime(data.generated_at, data.group_timezone))}</Banner> : null}
      {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
      {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
      {data && lessons.length === 0 ? <Empty title="Пар нет" body="В этот день занятий не запланировано." /> : null}
      {data && lessons.length > 0 ? (
        <div ref={ref} className="tline" style={{ height: (HR1 - HR0) * PX + 20 }}>
          {hours}
          {nowOn ? <div className="nowline" style={{ top: nowY }}><span>{clock}</span></div> : null}
          {lessons.map((lesson) => <Event key={lesson.id} lesson={lesson} timeZone={data.group_timezone} nowMs={nowMs} isToday={selected === 0} />)}
        </div>
      ) : null}
    </>
  );
}
