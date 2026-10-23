import { useEffect, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import type { HomeworkSummary, TodayResponse } from '@studgroup/shared-types';
import { STALE_AFTER_MS } from '../../api/cachePolicy';
import { useAcademicDeadlinesQuery, useScheduleQuery, useTodayQuery } from '../../api/queries';
import { AcademicDeadlineCard } from '../deadlines/AcademicDeadlineCard';
import { upcomingDeadlines } from '../deadlines/upcomingDeadlines';
import { ru } from '../../i18n/ru';
import { dayKey, formatLongDay, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { useProcessingPoll } from '../../lib/processingPoll';
import { TopBar } from '../../shell/TopBar';
import { Banner } from '../../ui/Banner';
import { Button } from '../../ui/Button';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { AlertCircle, Bolt, Check, Clock, NavTasks, Refresh } from '../../ui/icons';
import { mapNextLesson } from '../schedule/mapNextLesson';
import { NextClassCard } from '../schedule/NextClassCard';
import { HomeworkCard } from './HomeworkCard';
import { DayView, lessonsOfDay } from './DayView';
import { WeekStrip, weekKeys } from './WeekStrip';
import { useTaskList } from '../tasks/useTaskList';
import styles from './TodayScreen.module.css';

function Loading() {
  return (
    <div className={styles.list} role="status" aria-label={ru.states.loading}>
      {[0, 1, 2].map((i) => (
        <div key={i} className={styles.skelCard}>
          <Skeleton width={52} height={52} radius={16} />
          <div className={styles.skelText}>
            <Skeleton height={16} width="70%" />
            <Skeleton height={12} width="45%" />
            <Skeleton height={12} width="55%" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Today plan: only what is due today and not yet done; nothing "nearest" or "changed" is shown here. */
function Plan({ data, nowMs }: { data: TodayResponse; nowMs: number }) {
  const items = data.sections
    .filter((section) => section.kind === 'due_today')
    .flatMap((section) => section.items)
    .filter((item: HomeworkSummary) => item.my_state.completion !== 'completed' && !isHomeworkArchived(item, nowMs, data.group_timezone));
  if (items.length === 0) return null;
  return (
    <section className={styles.section} aria-labelledby="sec-plan">
      <h2 id="sec-plan" className={styles.sectionTitle}>{ru.today.plan}</h2>
      <div className={styles.list}>
        {items.map((item) => (
          <HomeworkCard key={item.id} item={item} nowMs={nowMs} timeZone={data.group_timezone} />
        ))}
      </div>
    </section>
  );
}

function Stats({ items }: { items: readonly HomeworkSummary[] }) {
  const live = items.filter((item) => item.status !== 'cancelled');
  const done = live.filter((item) => item.my_state.completion === 'completed').length;
  const urgent = live.filter((item) => item.my_state.completion !== 'completed' && item.urgency !== 'normal').length;
  const tiles: ReadonlyArray<readonly [string, number, ReactNode]> = [
    [ru.today.stats.total, live.length, <NavTasks key="t" size={18} />],
    [ru.today.stats.done, done, <Check key="d" size={18} />],
    [ru.today.stats.ahead, live.length - done, <Clock key="a" size={18} />],
    [ru.today.stats.urgent, urgent, <Bolt key="u" size={18} />],
  ];
  return (
    <div className={styles.stats} aria-label={ru.today.stats.aria}>
      {tiles.map(([label, value, icon]) => (
        <div key={label} className={styles.stat}>
          <span className={styles.statIcon} aria-hidden="true">{icon}</span>
          <b>{value}</b>
          <span>{label}</span>
        </div>
      ))}
    </div>
  );
}

export function TodayScreen() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const rawDay = Number(params.get('day'));
  const selected = Number.isInteger(rawDay) && rawDay > 0 && rawDay < 7 ? rawDay : 0;
  const query = useTodayQuery('today');
  const deadlines = useAcademicDeadlinesQuery();
  const all = useTaskList('all');
  const data = query.data;
  const nowMs = useServerNow(data?.server_time, query.dataUpdatedAt);
  const timeZone = data?.group_timezone;
  const today = timeZone ? dayKey(nowMs, timeZone) : null;
  const days = today ? weekKeys(today) : [];
  const schedule = useScheduleQuery(days[0] ?? '', days[6] ?? '', today !== null);
  const lessons = schedule.data?.lessons ?? [];
  const marked = new Set(timeZone ? days.filter((key) => lessonsOfDay(lessons, key, timeZone).length > 0) : []);
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
  const nextLesson = data ? mapNextLesson(data.next_lesson, nowMs, data.group_timezone) : null;
  const selectedKey = days[selected];
  const dateLine = data
    ? selected === 0
      ? formatLongDay(new Date(nowMs).toISOString(), data.group_timezone)
      : selectedKey
        ? formatLongDay(`${selectedKey}T12:00:00Z`, 'UTC')
        : ''
    : null;

  const deadlinesSection =
    deadlines.data && soonDeadlines.length ? (
      <section className={styles.section} aria-labelledby="important-deadlines">
        <h2 id="important-deadlines" className={styles.sectionTitle}>{ru.today.deadlines}</h2>
        <div className={styles.list}>
          {soonDeadlines.slice(0, 3).map((item) => (
            <AcademicDeadlineCard key={item.id} item={item} timeZone={deadlines.data!.group_timezone} />
          ))}
        </div>
      </section>
    ) : null;
  const select = (index: number) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (index === 0) next.delete('day');
      else next.set('day', String(index));
      return next;
    }, { replace: true });

  return (
    <>
      <TopBar />
      <div className={styles.page}>
        <h1 className={styles.title}>{ru.today.title}</h1>
        {dateLine !== null ? <p className={styles.date}>{dateLine}</p> : <Skeleton height={18} width={180} />}
        {today ? <WeekStrip days={days} selected={selected} marked={marked} onSelect={select} /> : null}

        {data && stale ? (
          <div className={styles.banner}>
            <Banner tone="warning" icon={<Clock />} action={<Button variant="ghost" onClick={() => void query.refetch()}><Refresh size={16} /></Button>}>
              {ru.freshness.stale(formatTime(data.freshness.last_successful_sync_at, data.group_timezone))}
            </Banner>
          </div>
        ) : null}
        {data && data.processing.state === 'delayed' ? (
          <div className={styles.banner}>
            <Banner tone="warning" icon={<AlertCircle />}>{ru.today.delayed}</Banner>
          </div>
        ) : null}
        {processingActive && gaveUp ? (
          <div className={styles.banner}>
            <Banner tone="info" action={<Button variant="secondary" onClick={restart}>{ru.today.recheck}</Button>}>{ru.today.pollStopped}</Banner>
          </div>
        ) : null}

        {query.isPending ? <Loading /> : null}
        {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}

        {data && selected === 0 ? (
          <>
            {all.items.length > 0 ? <Stats items={all.items} /> : null}
            {nextLesson ? (
              <div className={styles.banner}>
                <NextClassCard view={{ ...nextLesson, onOpen: () => void navigate(`/subjects/lessons/${encodeURIComponent(data.next_lesson!.lesson.id)}`, { state: { backTo: '/today' } }) }} />
              </div>
            ) : null}
            <Plan data={data} nowMs={nowMs} />
            {deadlinesSection}
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
            loading={schedule.isPending || all.isPending || deadlines.isPending}
          />
        ) : null}
      </div>
    </>
  );
}
