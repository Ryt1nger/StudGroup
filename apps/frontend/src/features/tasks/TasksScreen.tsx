import { useSearchParams } from 'react-router';
import clsx from 'clsx';
import { ru } from '../../i18n/ru';
import { useServerNow } from '../../lib/hooks';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { StateView } from '../../ui/StateView';
import { emptyIllustrations } from '../../ui/entityIcons';
import { ThemedImage } from '../../ui/ThemedImage';
import { HomeworkCard } from '../today/HomeworkCard';
import todayStyles from '../today/TodayScreen.module.css';
import { Banner } from '../../ui/Banner';
import { Button } from '../../ui/Button';
import { AlertCircle } from '../../ui/icons';
import { groupTaskSections, parseTaskFilter, TASK_FILTERS, type TaskFilter } from './taskFilters';
import { useTaskList } from './useTaskList';
import styles from './TasksScreen.module.css';

function Loading() {
  return (
    <div className={todayStyles.list} role="status" aria-label={ru.states.loading}>
      {[0, 1, 2].map((i) => (
        <div key={i} className={todayStyles.skelCard}>
          <Skeleton width={52} height={52} radius={16} />
          <div className={todayStyles.skelText}>
            <Skeleton height={16} width="70%" />
            <Skeleton height={12} width="45%" />
            <Skeleton height={12} width="55%" />
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * «Задания»: the full retained list (completed, cancelled and long-overdue items included), unlike Today.
 * The server applies the selected filter and paginates by cursor; the client only merges pages by id and
 * groups them. Cards are the Today cards; the filter lives in the URL so returning from a card keeps it.
 */
export function TasksScreen() {
  const [params, setParams] = useSearchParams();
  const filter = parseTaskFilter(params.get('filter'));
  const list = useTaskList(filter);
  const nowMs = useServerNow(list.generatedAt, list.updatedAt);
  const timeZone = list.timeZone;
  const liveItems = timeZone
    ? list.items.filter((item) => isHomeworkArchived(item, nowMs, timeZone) === (filter === 'archive'))
    : [];
  const sections = timeZone ? groupTaskSections(liveItems, nowMs, timeZone) : [];

  const select = (next: TaskFilter) => {
    if (next === filter) return;
    setParams(next === 'all' ? {} : { filter: next }, { replace: true });
  };
  const hasData = timeZone !== undefined;

  return (
    <>
      <div className={todayStyles.page}>
        <div className={styles.filters} role="group" aria-label={ru.tasks.filtersAria}>
          {TASK_FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className={clsx(styles.chip, f === filter && styles.chipActive)}
              aria-pressed={f === filter}
              onClick={() => select(f)}
            >
              {ru.tasks.filters[f]}
            </button>
          ))}
        </div>

        {list.isPending ? <Loading /> : null}
        {list.isError ? <RequestError error={list.error} onRetry={list.refetch} /> : null}
        <div className={clsx(styles.content, list.isSwitching && styles.switching)}>
          {hasData
            ? sections.map((section) => (
                <section
                  key={section.kind}
                  className={todayStyles.section}
                  aria-labelledby={`task-sec-${section.kind}`}
                >
                  <h2 id={`task-sec-${section.kind}`} className={todayStyles.sectionTitle}>
                    {ru.tasks.sections[section.kind as keyof typeof ru.tasks.sections]}
                  </h2>
                  <div className={todayStyles.list}>
                    {section.items.map((item) => (
                      <HomeworkCard key={item.id} item={item} nowMs={nowMs} timeZone={timeZone} archived={filter === 'archive'} />
                    ))}
                  </div>
                </section>
              ))
            : null}
          {hasData && list.loadMoreFailed ? (
            <div className={styles.more}>
              <Banner
                tone="warning"
                icon={<AlertCircle />}
                action={
                  <Button variant="secondary" onClick={() => void list.loadMore()}>
                    {ru.tasks.retry}
                  </Button>
                }
              >
                {ru.tasks.loadMoreFailed}
              </Banner>
            </div>
          ) : null}
          {hasData &&
          !list.isSwitching &&
          list.hasNextPage &&
          !list.loadMoreFailed &&
          list.items.length > 0 ? (
            <div className={styles.more}>
              <Button variant="secondary" busy={list.isFetchingNextPage} onClick={() => void list.loadMore()}>
                {list.isFetchingNextPage ? ru.tasks.loadingMore : ru.tasks.loadMore}
              </Button>
            </div>
          ) : null}
          {hasData &&
          !list.isSwitching &&
          !list.hasNextPage &&
          !list.isFetchingNextPage &&
          sections.length === 0 ? (
            <StateView
              illustration={<ThemedImage asset={emptyIllustrations.noTasks} />}
              title={ru.tasks.empty[filter].title}
              body={ru.tasks.empty[filter].body}
            />
          ) : null}
        </div>
      </div>
    </>
  );
}
