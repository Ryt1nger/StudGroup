import { Link } from 'react-router';
import { useSubjectsQuery } from '../../api/queries';
import { subjectGlyph, subjectStyle } from '../../lib/subjectColor';
import { SectionTop } from '../../shell/TopBar';
import { Empty, Skeleton } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';

export function SubjectsScreen() {
  const query = useSubjectsQuery();
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
