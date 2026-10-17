import { useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import type { HomeworkSummary, TodayResponse } from '@studgroup/shared-types';
import { STALE_AFTER_MS } from '../../api/cachePolicy';
import { useAcademicDeadlinesQuery, useTodayQuery } from '../../api/queries';
import { AcademicDeadlineCard } from '../deadlines/AcademicDeadlineCard';
import { upcomingDeadlines } from '../deadlines/upcomingDeadlines';
import { ru } from '../../i18n/ru';
import { addDays, dayKey, formatLongDay, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { useProcessingPoll } from '../../lib/processingPoll';
import { TopBar } from '../../shell/TopBar';
import { Banner } from '../../ui/Banner';
import { Button } from '../../ui/Button';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { StateView } from '../../ui/StateView';
import { emptyIllustrations } from '../../ui/entityIcons';
import { ThemedImage } from '../../ui/ThemedImage';
import { AlertCircle, ChevronRight, Clock, Refresh } from '../../ui/icons';
import { mapNextLesson } from '../schedule/mapNextLesson';
import { NextClassCard } from '../schedule/NextClassCard';
import { HomeworkCard } from './HomeworkCard';
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

function Sections({ data, nowMs, tomorrow }: { data: TodayResponse; nowMs: number; tomorrow: boolean }) {
  return (
    <>
      {data.sections.map((section) => {
        // Unknown future section kinds are skipped, never guessed.
        const title = tomorrow && section.kind === 'due_today' ? 'Срок завтра' : (ru.today.sections as Record<string, string | undefined>)[section.kind];
        const items = section.items.filter((item) => (item.my_state.completion !== 'completed' || item.deadline.state === 'known') && !isHomeworkArchived(item, nowMs, data.group_timezone));
        if (!title || items.length === 0) return null;
        return (
          <section key={section.kind} className={styles.section} aria-labelledby={`sec-${section.kind}`}>
            <h2 id={`sec-${section.kind}`} className={styles.sectionTitle}>
              {title}
            </h2>
            <div className={styles.list}>
              {items.map((item: HomeworkSummary) => (
                <HomeworkCard key={item.id} item={item} nowMs={nowMs} timeZone={data.group_timezone} />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}

export function TodayScreen() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tomorrow = params.get('day') === 'tomorrow';
  const query = useTodayQuery(tomorrow ? 'tomorrow' : 'today');
  const deadlines = useAcademicDeadlinesQuery();
  const soonDeadlines = deadlines.data ? upcomingDeadlines(deadlines.data.items, deadlines.data.generated_at, deadlines.data.group_timezone) : [];
  const data = query.data;
  const nowMs = useServerNow(data?.server_time, query.dataUpdatedAt);
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
  // The card follows `next_lesson` only; `empty` concerns homework and never hides the lesson.
  const selectedDay = data ? addDays(dayKey(nowMs, data.group_timezone), tomorrow ? 1 : 0) : null;
  const lessonInSelectedDay = data?.next_lesson && dayKey(data.next_lesson.lesson.starts_at, data.group_timezone) === selectedDay;
  const nextLesson = data && lessonInSelectedDay ? mapNextLesson(data.next_lesson, nowMs, data.group_timezone) : null;
  const hasCards = data ? data.sections.some((s) => s.items.some((item) => (item.my_state.completion !== 'completed' || item.deadline.state === 'known') && !isHomeworkArchived(item, nowMs, data.group_timezone))) : false;

  return (
    <>
      <TopBar />
      <div className={styles.page}>
        <div className={styles.daySwitch} aria-label="Выбор дня">
          {(['today', 'tomorrow'] as const).map((day) => {
            const active = tomorrow === (day === 'tomorrow');
            const label = day === 'today' ? ru.today.title : 'Завтра';
            const button = <button type="button" aria-pressed={active} className={active ? styles.activeDay : styles.inactiveDay} onClick={() => setParams((previous) => { const next = new URLSearchParams(previous); if (day === 'tomorrow') next.set('day', day); else next.delete('day'); return next; })}>{label}</button>;
            return <div key={day} className={styles.title} role={active ? 'heading' : undefined} aria-level={active ? 1 : undefined}>{button}</div>;
          })}
        </div>
        {data ? <p className={styles.date}>{tomorrow ? formatLongDay(`${addDays(dayKey(nowMs, data.group_timezone), 1)}T12:00:00Z`, 'UTC') : formatLongDay(new Date(nowMs).toISOString(), data.group_timezone)}</p> : <Skeleton height={18} width={180} />}

        {data && stale ? (
          <div className={styles.banner}>
            <Banner tone="warning" icon={<Clock />} action={<Button variant="ghost" onClick={() => void query.refetch()}><Refresh size={16} /></Button>}>
              {ru.freshness.stale(formatTime(data.freshness.last_successful_sync_at, data.group_timezone))}
            </Banner>
          </div>
        ) : null}

        {data && data.processing.state === 'delayed' ? (
          <div className={styles.banner}>
            <Banner tone="warning" icon={<AlertCircle />}>
              {ru.today.delayed}
            </Banner>
          </div>
        ) : null}
        {processingActive && gaveUp ? (
          <div className={styles.banner}>
            <Banner tone="info" action={<Button variant="secondary" onClick={restart}>{ru.today.recheck}</Button>}>
              {ru.today.pollStopped}
            </Banner>
          </div>
        ) : null}

        {nextLesson ? (
          <div className={styles.banner}>
            <NextClassCard view={{ ...nextLesson, onOpen: () => void navigate(`/subjects/lessons/${encodeURIComponent(data!.next_lesson!.lesson.id)}`, { state: { backTo: tomorrow ? '/today?day=tomorrow' : '/today' } }) }} />
          </div>
        ) : null}

        {data?.day_lessons_state === 'finished' || data?.day_lessons_state === 'empty' ? (
          <div className={styles.lessonNotice} role="status">
            <Clock size={24} />
            <p>{data.day_lessons_state === 'finished' ? 'На сегодня пары закончились' : tomorrow ? 'Завтра пар нет' : 'Сегодня пар нет'}</p>
          </div>
        ) : null}

        {query.isPending ? <Loading /> : null}
        {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
        {data && hasCards ? <Sections data={data} nowMs={nowMs} tomorrow={tomorrow} /> : null}
        {data && !hasCards && data.processing.state === 'idle' ? (
          <StateView illustration={<ThemedImage asset={emptyIllustrations.noTasks} />} title={tomorrow ? 'На завтра заданий нет' : ru.today.emptyTitle} body={tomorrow ? 'Задания с известным сроком на завтра появятся здесь.' : ru.today.emptyBody} />
        ) : null}
        {deadlines.isError ? <Banner tone="warning">Не удалось загрузить дедлайны. <Link to="/deadlines">Повторить</Link></Banner> : null}
        {deadlines.data && soonDeadlines.length ? <section className={styles.section} aria-labelledby="important-deadlines"><h2 id="important-deadlines" className={styles.sectionTitle}>Дедлайны</h2><div className={styles.list}>{soonDeadlines.slice(0, 3).map((item) => <AcademicDeadlineCard key={item.id} item={item} timeZone={deadlines.data.group_timezone} />)}</div><Link className={styles.allDeadlines} to="/deadlines" viewTransition><span>Все дедлайны</span><ChevronRight size={20} /></Link></section> : null}
      </div>
    </>
  );
}
