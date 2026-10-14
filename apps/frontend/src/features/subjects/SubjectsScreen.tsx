import { useSubjectsQuery } from '../../api/queries';
import { TopBar } from '../../shell/TopBar';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import styles from './LessonSubjectScreen.module.css';

export function SubjectsScreen() {
  const query = useSubjectsQuery();
  return (
    <>
      <TopBar />
      <div className={styles.page}>
        <h1 className={styles.title}>Предметы</h1>
        {query.isPending ? (
          <div role="status" aria-label="Загрузка">
            <Skeleton height={92} />
            <Skeleton height={92} />
          </div>
        ) : null}
        {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
        {query.data ? (
          query.data.items.length ? (
            <div className={styles.list}>
              {query.data.items.map((subject) => (
                <section key={subject.id} className={styles.panel} aria-label={subject.name}>
                  <h2>{subject.name}</h2>
                  <p className={styles.note}>Заданий: {subject.homework_count}</p>
                  <p className={styles.note}>Событий и контрольных: {subject.deadline_count}</p>
                </section>
              ))}
            </div>
          ) : (
            <p className={styles.panel}>Предметы пока не найдены.</p>
          )
        ) : null}
      </div>
    </>
  );
}
