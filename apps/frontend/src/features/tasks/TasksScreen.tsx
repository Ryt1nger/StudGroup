import clsx from 'clsx';
import { NavLink, useSearchParams } from 'react-router';
import type { HomeworkSummary } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { useServerNow } from '../../lib/hooks';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { GroupTop } from '../../shell/TopBar';
import { Banner, Empty, Skeleton } from '../../ui/Blocks';
import { EndSentinel } from '../../ui/EndSentinel';
import { RequestError } from '../../ui/RequestError';
import { HwCard } from '../shared/Cards';
import { useToggleDone } from '../shared/useToggleDone';
import { parseTaskFilter, TASK_FILTERS, type TaskFilter } from './taskFilters';
import { useTaskList } from './useTaskList';

/** The two titles of the tasks area: «Задания» and «Дедлайны» switch in place. */
export function SectionTitles({ active }: { active: 'tasks' | 'deadlines' }) {
  return (
    <div className="titles" role="group" aria-label="Задания и дедлайны">
      {(
        [
          ['/tasks', 'Задания', 'tasks'],
          ['/deadlines', 'Дедлайны', 'deadlines'],
        ] as const
      ).map(([to, label, key]) => (
        <NavLink key={to} to={to} replace className={clsx('tt', active === key && 'on')} aria-current={active === key ? 'page' : undefined}>
          {label}
        </NavLink>
      ))}
    </div>
  );
}

const deadlineOf = (item: HomeworkSummary) => (item.deadline.state === 'known' && item.deadline.at ? Date.parse(item.deadline.at) : Number.POSITIVE_INFINITY);

/** Deadline order; completed cards sink to the end of the list, the order inside each group is kept. */
function arrange(items: readonly HomeworkSummary[], archive: boolean): HomeworkSummary[] {
  const done = (item: HomeworkSummary) => (item.my_state.completion === 'completed' ? 1 : 0);
  return [...items].sort((a, b) => done(a) - done(b) || (archive ? deadlineOf(b) - deadlineOf(a) : deadlineOf(a) - deadlineOf(b)));
}

/**
 * «Задания»: the server applies the filter and paginates by cursor; the client merges pages by id.
 * Archived cards (past deadline, cancelled) are shown only under «Архив», grey and read-only.
 */
export function TasksScreen() {
  const [params, setParams] = useSearchParams();
  const filter = parseTaskFilter(params.get('filter'));
  const list = useTaskList(filter);
  const nowMs = useServerNow(list.generatedAt, list.updatedAt);
  const timeZone = list.timeZone;
  const { toggle, busy } = useToggleDone();
  const archive = filter === 'archive';
  const shown = timeZone ? arrange(list.items.filter((item) => isHomeworkArchived(item, nowMs, timeZone) === archive), archive) : [];
  const hasData = timeZone !== undefined;

  const select = (next: TaskFilter) => {
    if (next === filter) return;
    setParams(next === 'all' ? {} : { filter: next }, { replace: true });
  };

  return (
    <>
      <GroupTop />
      <h1 className="sr">{ru.tasks.title}</h1>
      <SectionTitles active="tasks" />
      <div className="seg" role="group" aria-label={ru.tasks.filtersAria}>
        {TASK_FILTERS.map((f) => (
          <button key={f} type="button" className="chip" aria-pressed={f === filter} onClick={() => select(f)}>
            {ru.tasks.filters[f]}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 16, opacity: list.isSwitching ? 0.6 : 1, transition: 'opacity .15s' }}>
        {list.isPending ? <Skeleton label={ru.states.loading} /> : null}
        {list.isError ? <RequestError error={list.error} onRetry={list.refetch} /> : null}
        {hasData && timeZone
          ? shown.map((item) => (
              <HwCard key={item.id} item={item} nowMs={nowMs} timeZone={timeZone} archive={archive} busy={busy.has(item.id)} onToggle={(card) => void toggle(card)} />
            ))
          : null}
        {hasData && list.loadMoreFailed ? (
          <Banner tone="warning" icon="alert" action={<button type="button" className="bact txt" onClick={() => void list.loadMore()}>{ru.tasks.retry}</button>}>
            {ru.tasks.loadMoreFailed}
          </Banner>
        ) : null}
        {hasData && !list.isSwitching && list.hasNextPage && !list.loadMoreFailed && list.items.length > 0 ? (
          <EndSentinel busy={list.isFetchingNextPage} onReach={() => !list.isFetchingNextPage && void list.loadMore()} />
        ) : null}
        {hasData && !list.isSwitching && !list.hasNextPage && !list.isFetchingNextPage && shown.length === 0 ? (
          <Empty title={ru.tasks.empty[filter].title} body={ru.tasks.empty[filter].body} />
        ) : null}
      </div>
    </>
  );
}
