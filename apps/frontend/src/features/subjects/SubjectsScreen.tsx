import { useSubjectsQuery } from '../../api/queries';
import { subjectGlyph, subjectStyle } from '../../lib/subjectColor';
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
                <section
                  key={subject.id}
                  className={styles.subject}
                  aria-label={subject.name}
                  style={subjectStyle(subject.name)}
                >
                  <span className={styles.glyph} aria-hidden="true">
                    {subjectGlyph(subject.name)}
                  </span>
                  <div className={styles.subjectText}>
                    <h2>{subject.name}</h2>
                    <p className={styles.note}>
                      Заданий: {subject.homework_count} · Событий и КТ: {subject.deadline_count}
                    </p>
                  </div>
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
