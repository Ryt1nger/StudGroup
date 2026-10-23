import { useState } from 'react';
import type { AcademicDeadline } from '@studgroup/shared-types';
import { useAcademicDeadlinesQuery } from '../../api/queries';
import { useIsEditor } from '../../session/SessionProvider';
import { useServerNow } from '../../lib/hooks';
import { GroupTop } from '../../shell/TopBar';
import { Empty, Skeleton } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { DlCard } from '../shared/Cards';
import { SectionTitles } from '../tasks/TasksScreen';

type View = 'actual' | 'review' | 'archive';

const startOf = (item: AcademicDeadline) => Date.parse(item.deadline_at ?? item.window_start ?? '') || Number.POSITIVE_INFINITY;

export function DeadlinesScreen() {
  const [view, setView] = useState<View>('actual');
  const editor = useIsEditor();
  const query = useAcademicDeadlinesQuery(view === 'archive', view === 'review');
  // «Проверка» exists only for editors and only while something needs a look; the count comes with every list.
  const reviewCount = query.data?.review_count ?? 0;
  const showReview = editor && (reviewCount > 0 || view === 'review');
  const VIEWS: ReadonlyArray<readonly [View, string]> = [
    ['actual', 'Актуальные'],
    ...(showReview ? ([['review', 'Проверка']] as const) : []),
    ['archive', 'Архив'],
  ];
  const data = query.data;
  const nowMs = useServerNow(data?.generated_at, query.dataUpdatedAt);
  const items = data ? [...data.items].sort((a, b) => (view === 'archive' ? startOf(b) - startOf(a) : startOf(a) - startOf(b))) : [];

  return (
    <>
      <GroupTop />
      <h1 className="sr">Дедлайны</h1>
      <SectionTitles active="deadlines" />
      <div className="seg" role="group" aria-label="Раздел дедлайнов">
        {VIEWS.map(([key, label]) => (
          <button key={key} type="button" className="chip" aria-pressed={view === key} onClick={() => setView(key)}>
            {label}
          </button>
        ))}
      </div>
      <div style={{ marginTop: 16 }}>
        {query.isPending ? <Skeleton label="Загружаем…" /> : null}
        {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
        {data ? items.map((item) => <DlCard key={item.id} item={item} timeZone={data.group_timezone} nowMs={nowMs} archive={view === 'archive'} />) : null}
        {data && items.length === 0 ? view === 'review' ? <Empty title="Всё проверено" body="События с неясной датой появятся здесь." /> : <Empty title="Дедлайнов пока нет" body="Контрольные, тесты и контрольные точки появятся здесь." /> : null}
      </div>
    </>
  );
}
