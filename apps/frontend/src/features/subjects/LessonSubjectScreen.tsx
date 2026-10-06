import { useCallback } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { useLessonSubjectQuery } from '../../api/queries';
import { formatLongDay, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { TopBar } from '../../shell/TopBar';
import { useBackButton } from '../../shell/useBackButton';
import { Button } from '../../ui/Button';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { ChevronLeft } from '../../ui/icons';
import { HomeworkCard } from '../today/HomeworkCard';
import { AcademicDeadlineCard } from '../deadlines/AcademicDeadlineCard';
import styles from './LessonSubjectScreen.module.css';

/** Real lesson context; dates/subject/data come from the scoped backend, not route text. */
export function LessonSubjectScreen() {
  const { lessonId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const backTo = location.state?.backTo === '/today?day=tomorrow' ? '/today?day=tomorrow' : '/today';
  const back = useCallback(() => { void navigate(backTo); }, [navigate, backTo]);
  const nativeBack = useBackButton(back);
  const query = useLessonSubjectQuery(lessonId);
  const data = query.data;
  const nowMs = useServerNow(data?.generated_at, query.dataUpdatedAt);
  return (
    <>
      <TopBar />
      <div className={styles.page}>
        {!nativeBack ? <Button variant="ghost" icon={<ChevronLeft />} onClick={back}>Назад</Button> : null}
        {query.isPending ? <div role="status" aria-label="Загрузка"><Skeleton height={44} /><Skeleton height={120} /></div> : null}
        {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
        {data ? (
          <>
            <h1 className={styles.title}>{data.lesson.subject}</h1>
            <p className={styles.date}>{formatLongDay(data.lesson.starts_at, data.group_timezone)}</p>
            <section className={styles.panel} aria-label="Выбранная пара">
              <p className={styles.time}>{formatTime(data.lesson.starts_at, data.group_timezone)} — {formatTime(data.lesson.ends_at, data.group_timezone)}</p>
              {data.lesson.location ? <p className={styles.note}>{data.lesson.location}</p> : null}
              {data.lesson.teacher ? <p className={styles.note}>{data.lesson.teacher}</p> : null}
            </section>
            <section className={styles.section} aria-labelledby="subject-homework">
              <h2 id="subject-homework">Домашние задания</h2>
              {data.homework.length ? <div className={styles.list}>{data.homework.map((item) => <HomeworkCard key={item.id} item={item} nowMs={nowMs} timeZone={data.group_timezone} />)}</div> : <p className={styles.panel}>Связанные ДЗ пока не найдены.</p>}
              {data.homework_total > data.homework.length ? <Link to="/tasks">Все задания — {data.homework_total}</Link> : null}
            </section>
            <section className={styles.section} aria-labelledby="subject-events">
              <h2 id="subject-events">Ближайшие события и КТ</h2>
              {data.events_state === 'ready' ? <div className={styles.list}>{data.events?.length ? data.events.map((item) => <AcademicDeadlineCard key={item.id} item={item} timeZone={data.group_timezone} />) : <p className={styles.panel}>Ближайшие события пока не найдены.</p>}</div> : <p className={styles.panel}>События и КТ пока не подключены.</p>}
            </section>
            <section className={styles.section} aria-labelledby="subject-materials">
              <h2 id="subject-materials">Материалы</h2>
              <p className={styles.panel}>Материалы пока не подключены.</p>
            </section>
          </>
        ) : null}
      </div>
    </>
  );
}
