import { useEffect } from 'react';
import { useSearchParams } from 'react-router';
import type { HomeworkSummary, TodayResponse } from '@studgroup/shared-types';
import { STALE_AFTER_MS } from '../../api/cachePolicy';
import { useAcademicDeadlinesQuery, useScheduleQuery, useTodayQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { dayKey, formatLongDay, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { useProcessingPoll } from '../../lib/processingPoll';
import { SectionTop } from '../../shell/TopBar';
import { Banner, Skeleton } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';
import { RequestError } from '../../ui/RequestError';
import { EventCards } from '../events/EventCard';
import { useEventCapabilities, useGroupEvents } from '../events/api';
import { groupEventOnDay } from '../events/calendar';
import { upcomingDeadlines } from '../deadlines/upcomingDeadlines';
import { DlCard, HwCard } from '../shared/Cards';
import { Hero } from '../shared/Hero';
import { Sec } from '../shared/Sec';
import { WeekStrip, weekKeys } from '../shared/WeekStrip';
import { useTaskList } from '../tasks/useTaskList';
import { useToggleDone } from '../shared/useToggleDone';
import { DayView, lessonsOfDay } from './DayView';

function Plan({ data, nowMs, busy, onToggle }: { data: TodayResponse; nowMs: number; busy: ReadonlySet<string>; onToggle: (item: HomeworkSummary) => void }) {
  const items = data.sections
    .filter((section) => section.kind === 'due_today')
    .flatMap((section) => section.items)
    .filter((item: HomeworkSummary) => item.my_state.completion !== 'completed' && !isHomeworkArchived(item, nowMs, data.group_timezone));
  if (items.length === 0) return null;
  return (
    <Sec id="sec-plan" title={ru.today.plan}>
      {items.map((item) => (
        <HwCard key={item.id} item={item} nowMs={nowMs} timeZone={data.group_timezone} busy={busy.has(item.id)} onToggle={onToggle} />
      ))}
    </Sec>
  );
}

function Stats({ items }: { items: readonly HomeworkSummary[] }) {
  const live = items.filter((item) => item.status !== 'cancelled' && !isOver(item));
  const done = live.filter((item) => item.my_state.completion === 'completed').length;
  const urgent = live.filter((item) => item.my_state.completion !== 'completed' && item.urgency !== 'normal').length;
  const tiles = [
    ['s1', 'list', live.length, ru.today.stats.total],
    ['s2', 'check', done, ru.today.stats.done],
    ['s3', 'clock', live.length - done, ru.today.stats.ahead],
    ['s4', 'flame', urgent, ru.today.stats.urgent],
  ] as const;
  return (
    <div className="stats" aria-label={ru.today.stats.aria}>
      {tiles.map(([cls, icon, value, label]) => (
        <div key={cls} className={`stat ${cls}`}>
          <span className="si"><Ic name={icon} size={18} /></span>
          <b>{value}</b>
          <span>{label}</span>
        </div>
      ))}
    </div>
  );
}

function isOver(item: HomeworkSummary): boolean {
  return item.deadline.state === 'known' && !!item.deadline.at && Date.parse(item.deadline.at) < Date.now() && item.my_state.completion !== 'completed';
}

export function TodayScreen() {
  const [params, setParams] = useSearchParams();
  const rawDay = Number(params.get('day'));
  const selected = Number.isInteger(rawDay) && rawDay > 0 && rawDay < 7 ? rawDay : 0;
  const query = useTodayQuery('today');
  const deadlines = useAcademicDeadlinesQuery();
  const all = useTaskList('all');
  const { toggle, busy } = useToggleDone();
  const data = query.data;
  const nowMs = useServerNow(data?.server_time, query.dataUpdatedAt);
  const timeZone = data?.group_timezone;
  const today = timeZone ? dayKey(nowMs, timeZone) : null;
  const days = today ? weekKeys(today) : [];
  const schedule = useScheduleQuery(days[0] ?? '', days[6] ?? '', today !== null);
  const features = useEventCapabilities();
  const groupEvents = useGroupEvents({ start: days[0] ?? '', end: days[6] ?? '' });
  const eventItems = groupEvents.data?.events ?? [];
  const lessons = schedule.data?.lessons ?? [];
  const marked = new Set(timeZone ? days.filter((key) => lessonsOfDay(lessons, key, timeZone).length > 0 || eventItems.some((item) => groupEventOnDay(item, key, timeZone))) : []);
  const soonDeadlines = deadlines.data ? upcomingDeadlines(deadlines.data.items, deadlines.data.generated_at, deadlines.data.group_timezone) : [];
  const refetch = query.refetch;
  useEffect(() => {
    if (query.isError || data?.day_lessons_state !== 'scheduled' || !data.next_lesson) return;
    // Re-check the backend at the lesson boundary; never infer that the whole day
    // finished just because this one lesson ended. Cleanup also covers navigation.
    const delay = Math.max(0, Date.parse(data.next_lesson.lesson.ends_at) - nowMs) + 100;
    const timer = window.setTimeout(() => { void refetch(); }, delay);
    return () => window.clearTimeout(timer);
  }, [data, nowMs, refetch, query.isError]);

  const processingActive = data !== undefined && data.processing.state !== 'idle';
  const { gaveUp, restart } = useProcessingPoll(processingActive, query.refetch);
  const staleByClock = data ? nowMs - Date.parse(data.freshness.last_successful_sync_at) > STALE_AFTER_MS : false;
  const stale = data ? data.freshness.stale || staleByClock || (query.isError && query.data !== undefined) : false;
  const selectedKey = days[selected];

  const select = (index: number) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (index === 0) next.delete('day');
      else next.set('day', String(index));
      return next;
    }, { replace: true });

  const deadlinesSection =
    deadlines.data && soonDeadlines.length ? (
      <Sec id="important-deadlines" title={ru.today.deadlines}>
        {soonDeadlines.slice(0, 3).map((item) => (
          <DlCard key={item.id} item={item} timeZone={deadlines.data!.group_timezone} nowMs={nowMs} />
        ))}
      </Sec>
    ) : null;

  return (
    <>
      <SectionTop />
      <h1 className="sr">{ru.today.title}</h1>
      {today ? <WeekStrip days={days} selected={selected} marked={marked} onSelect={select} /> : <Skeleton count={1} label={ru.states.loading} />}
      {selected > 0 && data && selectedKey ? <div className="datel">{formatLongDay(`${selectedKey}T12:00:00Z`, 'UTC')}</div> : null}

      {data && stale ? (
        <Banner
          tone="warning"
          icon="clock"
          action={<button type="button" className="bact" aria-label="Обновить" onClick={() => void query.refetch()}><Ic name="refresh" size={16} /></button>}
        >
          {ru.freshness.stale(formatTime(data.freshness.last_successful_sync_at, data.group_timezone))}
        </Banner>
      ) : null}
      {data && data.processing.state === 'delayed' ? <Banner tone="warning" icon="alert">{ru.today.delayed}</Banner> : null}
      {processingActive && gaveUp ? (
        <Banner tone="info" icon="alert" action={<button type="button" className="bact txt" onClick={restart}>{ru.today.recheck}</button>}>{ru.today.pollStopped}</Banner>
      ) : null}

      {features.events && groupEvents.isError ? <RequestError error={groupEvents.error} onRetry={() => void groupEvents.refetch()} /> : null}
      {features.events && groupEvents.isPending ? <Skeleton count={1} label="Загрузка событий календаря" /> : null}
      {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
      {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}

      {data && selected === 0 ? (
        <>
          {all.items.length > 0 ? <Stats items={all.items} /> : null}
          {data.next_lesson ? <Hero next={data.next_lesson} nowMs={nowMs} timeZone={data.group_timezone} /> : null}
          <Plan data={data} nowMs={nowMs} busy={busy} onToggle={(item) => void toggle(item)} />
          {deadlinesSection}
          {today && eventItems.some((item) => groupEventOnDay(item, today, data.group_timezone)) ? <Sec id="today-group-events" title="События"><EventCards items={eventItems.filter((item) => groupEventOnDay(item, today, data.group_timezone))} timeZone={data.group_timezone} /></Sec> : null}
        </>
      ) : null}
      {data && selected > 0 && selectedKey ? (
        <DayView
          dayKeyValue={selectedKey}
          timeZone={data.group_timezone}
          nowMs={nowMs}
          lessons={lessons}
          homework={all.items}
          events={deadlines.data?.items ?? []}
          groupEvents={eventItems}
          loading={schedule.isPending || all.isPending || deadlines.isPending || (features.events === true && groupEvents.isPending)}
        />
      ) : null}
    </>
  );
}

