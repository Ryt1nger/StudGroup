import { Link } from 'react-router';
import { useSubjectsQuery } from '../../api/queries';
import { subjectGlyph, subjectStyle } from '../../lib/subjectColor';
import { SectionTop } from '../../shell/TopBar';
import { Empty, Skeleton } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { confirmedSubject } from '../homework/confirmedSubject';
import { useTaskList } from '../tasks/useTaskList';

export function SubjectsScreen() {
  const query = useSubjectsQuery();
  const tasks = useTaskList('all');
  // Progress: the share of the subject's cards the user has completed, from the cards already loaded.
  const progress = (name: string): number | null => {
    if (!tasks.items.length) return null;
    const own = tasks.items.filter((item) => item.status !== 'cancelled' && (confirmedSubject(item) ?? item.subject).name === name);
    if (!own.length) return null;
    return Math.round((own.filter((item) => item.my_state.completion === 'completed').length / own.length) * 100);
  };
  return (
    <>
      <SectionTop title="Предметы" />
      <h1 className="sr">Предметы</h1>
      {query.isPending ? <Skeleton label="Загрузка" /> : null}
      {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.data ? (
        query.data.items.length ? (
          <div className="grid2">
            {query.data.items.map((subject) => {
              return (
                <Link key={subject.id} className="tile" style={subjectStyle(subject.name)} to={`/subjects/${encodeURIComponent(subject.id)}`} aria-label={subject.name}>
                  <span className="bar" />
                  <div className="ic">{subjectGlyph(subject.name)}</div>
                  <div>
                    <b>{subject.name}</b>
                    {progress(subject.name) !== null ? <div className="prog" aria-hidden="true"><i style={{ width: `${progress(subject.name)}%` }} /></div> : null}
                  </div>
                </Link>
              );
            })}
          </div>
        ) : (
          <Empty title="Предметы пока не найдены" body="Они появятся, когда в группе будут задания и пары." />
        )
      ) : null}
    </>
  );
}
