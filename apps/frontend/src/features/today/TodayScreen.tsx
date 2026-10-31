import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router';
import type { HomeworkSummary, TodayResponse } from '@studgroup/shared-types';
import { STALE_AFTER_MS } from '../../api/cachePolicy';
import { useAcademicDeadlinesQuery, useScheduleQuery, useTodayQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { addDays, dayKey, formatLongDay, formatTime, plural } from '../../lib/format';
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
import { useToggleDone } from '../shared/useToggleDone';
import { DayView, lessonsOfDay } from './DayView';
import { homeworkStats } from './homeworkStats';
import { useAllHomework } from './useAllHomework';

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
  const counts = homeworkStats(items);
  const tiles = [
    ['s1', 'list', counts.total, ru.today.stats.total],
    ['s2', 'check', counts.done, ru.today.stats.done],
    ['s3', 'clock', counts.ahead, ru.today.stats.ahead],
    ['s4', 'flame', counts.urgent, ru.today.stats.urgent],
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

function relativeLabel(key: string, today: string): string {
  const diff = Math.round((Date.parse(`${key}T12:00:00Z`) - Date.parse(`${today}T12:00:00Z`)) / 86_400_000);
  if (diff === 0) return 'Сегодня';
  if (diff === 1) return 'Завтра';
  if (diff === 2) return 'Послезавтра';
  return diff > 0 ? `Через ${diff} ${plural(diff, 'день', 'дня', 'дней')}` : formatLongDay(`${key}T12:00:00Z`, 'UTC');
}

export function TodayScreen() {
  const [params, setParams] = useSearchParams();
  const query = useTodayQuery('today');
  const deadlines = useAcademicDeadlinesQuery();
  const all = useAllHomework();
  const allItems = all.data ?? [];
  const { toggle, busy } = useToggleDone();
  const data = query.data;
  const nowMs = useServerNow(data?.server_time, query.dataUpdatedAt);
  const timeZone = data?.group_timezone;
  const today = timeZone ? dayKey(nowMs, timeZone) : null;
  const rawSelected = params.get('date');
  const legacyDay = Number(params.get('day'));
  const manualSelected = Boolean(rawSelected) || (Number.isInteger(legacyDay) && legacyDay > 0 && legacyDay < 7);
  const selectedKey = rawSelected && /^\d{4}-\d{2}-\d{2}$/.test(rawSelected) ? rawSelected : today && manualSelected ? addDays(today, legacyDay) : today;
  const days = selectedKey ? weekKeys(selectedKey) : [];
  const selected = selectedKey ? days.indexOf(selectedKey) : -1;
  const schedule = useScheduleQuery(days[0] ?? '', days[6] ?? '', today !== null);
  const features = useEventCapabilities();
  const groupEvents = useGroupEvents({ start: days[0] ?? '', end: days[6] ?? '' });
  const eventItems = groupEvents.data?.events ?? [];
  const future = useScheduleQuery(today ?? '', today ? addDays(today, 31) : '', today !== null && !manualSelected);
  const futureEvents = useGroupEvents({ start: today ?? '', end: today ? addDays(today, 31) : '' });
  const lessons = schedule.data?.lessons ?? [];
  const marked = new Set(timeZone ? days.filter((key) => lessonsOfDay(lessons, key, timeZone).length > 0 || eventItems.some((item) => groupEventOnDay(item, key, timeZone))) : []);
  const soonDeadlines = deadlines.data ? upcomingDeadlines(deadlines.data.items, deadlines.data.generated_at, deadlines.data.group_timezone) : [];
  const autoChecked = useRef(false);
  useEffect(() => {
    if (autoChecked.current || manualSelected || !today || !timeZone || !data || !future.data || !deadlines.data || !all.data || (features.events && !futureEvents.data)) return;
    autoChecked.current = true;
    const active = (key: string) => {
      const homework = all.data.some((item) => item.status !== 'cancelled' && item.my_state.completion !== 'completed' && item.deadline.state === 'known' && item.deadline.at && dayKey(item.deadline.at, timeZone) === key && !isHomeworkArchived(item, nowMs, timeZone));
      const lessons = lessonsOfDay(future.data.lessons, key, timeZone).some((lesson) => Date.parse(lesson.ends_at) > nowMs);
      const events = deadlines.data.items.some((item) => item.deadline_at ? dayKey(item.deadline_at, timeZone) === key && (item.date_only || Date.parse(item.deadline_at) > nowMs) : item.window_start && item.window_end && dayKey(item.window_start, timeZone) <= key && dayKey(new Date(Date.parse(item.window_end) - 1), timeZone) >= key && Date.parse(item.window_end) > nowMs);
      const publishedEvents = futureEvents.data?.events.some((item) => groupEventOnDay(item, key, timeZone) && (!item.ends_at || Date.parse(item.ends_at) > nowMs)) ?? false;
      return homework || lessons || events || publishedEvents;
    };
    if (active(today)) return;
    for (let offset = 1; offset <= 31; offset += 1) {
      const key = addDays(today, offset);
      if (active(key)) { setParams((previous) => { const next = new URLSearchParams(previous); next.set('date', key); return next; }, { replace: true }); break; }
    }
  }, [manualSelected, today, timeZone, data, future.data, futureEvents.data, features.events, deadlines.data, all.data, nowMs, setParams]);
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
  const select = (index: number) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set('date', days[index]!);
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
      {today ? <WeekStrip days={days} selected={selected} marked={marked} onSelect={select} onWeekChange={(direction) => {
        const key = addDays(days[0]!, direction * 7);
        setParams((previous) => { const next = new URLSearchParams(previous); next.set('date', key); return next; }, { replace: true });
      }} /> : <Skeleton count={1} label={ru.states.loading} />}
      {data && selectedKey && today ? <div className="datel">{relativeLabel(selectedKey, today)} · {formatLongDay(`${selectedKey}T12:00:00Z`, 'UTC')}</div> : null}

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

      {data && selectedKey === today ? (
        <>
          {all.data ? <Stats items={allItems} /> : null}
          {data.next_lesson ? <Hero next={data.next_lesson} nowMs={nowMs} timeZone={data.group_timezone} /> : null}
          <Plan data={data} nowMs={nowMs} busy={busy} onToggle={(item) => void toggle(item)} />
          {deadlinesSection}
          {today && eventItems.some((item) => groupEventOnDay(item, today, data.group_timezone)) ? <Sec id="today-group-events" title="События"><EventCards items={eventItems.filter((item) => groupEventOnDay(item, today, data.group_timezone))} timeZone={data.group_timezone} /></Sec> : null}
          {today && !data.next_lesson && !data.sections.some((section) => section.kind === 'due_today' && section.items.some((item) => item.my_state.completion !== 'completed' && !isHomeworkArchived(item, nowMs, data.group_timezone))) && !eventItems.some((item) => groupEventOnDay(item, today, data.group_timezone)) && !soonDeadlines.some((item) => item.deadline_at && dayKey(item.deadline_at, data.group_timezone) === today) ? <div className="empty"><b>Сегодня больше ничего нет</b></div> : null}
        </>
      ) : null}
      {data && selectedKey !== today && selectedKey ? (
        <DayView
          dayKeyValue={selectedKey}
          timeZone={data.group_timezone}
          nowMs={nowMs}
          lessons={lessons}
          homework={allItems}
          events={deadlines.data?.items ?? []}
          groupEvents={eventItems}
          loading={schedule.isPending || all.isPending || deadlines.isPending || (features.events === true && groupEvents.isPending)}
        />
      ) : null}
    </>
  );
}
