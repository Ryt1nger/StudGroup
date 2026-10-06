import clsx from 'clsx';
import { useSearchParams } from 'react-router';
import { useAcademicDeadlinesQuery } from '../../api/queries';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { AcademicDeadlineCard } from './AcademicDeadlineCard';
import styles from './Deadlines.module.css';
import filterStyles from '../tasks/TasksScreen.module.css';

export function DeadlinesScreen() {
  const [params, setParams] = useSearchParams();
  const archive = params.get('archive') === 'true';
  const query = useAcademicDeadlinesQuery(archive);
  return <div className={styles.page}>
    <div className={clsx(filterStyles.filters, styles.filters)} role="group" aria-label="Раздел дедлайнов">
      <button type="button" className={clsx(filterStyles.chip, !archive && filterStyles.chipActive)} aria-pressed={!archive} onClick={() => setParams({})}>Актуальные</button>
      <button type="button" className={clsx(filterStyles.chip, archive && filterStyles.chipActive)} aria-pressed={archive} onClick={() => setParams({ archive: 'true' })}>Архив</button>
    </div>
    {query.isPending ? <Skeleton height={100} /> : null}
    {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
    {query.data ? <div className={styles.list}>{query.data.items.map((item) => <AcademicDeadlineCard key={item.id} item={item} timeZone={query.data.group_timezone} />)}{query.data.items.length === 0 ? <p>Дедлайны пока не найдены.</p> : null}</div> : null}
  </div>;
}
