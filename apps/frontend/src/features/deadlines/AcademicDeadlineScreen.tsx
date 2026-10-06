import { useCallback } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { useAcademicDeadlineQuery } from '../../api/queries';
import { useIsHeadman } from '../../session/SessionProvider';
import { TopBar } from '../../shell/TopBar';
import { useBackButton } from '../../shell/useBackButton';
import { Button } from '../../ui/Button';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { ChevronLeft, Clock } from '../../ui/icons';
import { formatEventDate } from './formatEventDate';
import styles from './Deadlines.module.css';

export function AcademicDeadlineScreen() {
  const { deadlineId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const hint: unknown = location.state?.backTo;
  const backTo = typeof hint === 'string' && /^\/(?:today|deadlines|subjects\/lessons\/[\w:.-]+)(?:\?[^#]*)?$/.test(hint) ? hint : '/deadlines';
  const back = useCallback(() => { void navigate(backTo); }, [navigate, backTo]);
  const nativeBack = useBackButton(back);
  const query = useAcademicDeadlineQuery(deadlineId);
  const headman = useIsHeadman();
  const data = query.data;
  const item = data?.item;
  const labels = { control_point: 'Контрольная точка', assessment: 'Контрольная работа', test: 'Тест' };
  return <><TopBar /><div className={styles.page}>
    {!nativeBack ? <Button variant="ghost" icon={<ChevronLeft />} onClick={back}>Назад</Button> : null}
    {query.isPending ? <div role="status" aria-label="Загрузка события"><Skeleton height={44} /><Skeleton height={160} /></div> : null}
    {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
    {item && data ? <>
      <h1 className={styles.detailTitle}>{item.subject}</h1>
      <section className={styles.card}>
        <h2 className={styles.detailTask}>{item.title}</h2>
        <p>{labels[item.kind]}</p>
        <p className={styles.when}><Clock size={16} /><span>{formatEventDate(item, data.group_timezone)}</span></p>
      </section>
      <section className={`${styles.card} ${styles.detailBody}`} aria-labelledby="event-task">
        <h2 id="event-task" className={styles.detailTask}>Что нужно сделать</h2>
        <p>{item.description}</p>
      </section>
      {item.date_hint && (headman || (!item.deadline_at && !item.window_start)) ? <section className={`${styles.card} ${styles.detailBody}`}><p className={styles.warning}>{item.date_hint}</p></section> : null}
    </> : null}
  </div></>;
}
