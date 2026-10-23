import { useParams } from 'react-router';
import { useLessonSubjectQuery } from '../../api/queries';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { useServerNow } from '../../lib/hooks';
import { BackTop } from '../../shell/TopBar';
import { Skeleton } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { SubjectView } from './SubjectView';

/** The subject of a real lesson: homework, events and materials come from the scoped backend response. */
export function LessonSubjectScreen() {
  const { lessonId = '' } = useParams();
  const query = useLessonSubjectQuery(lessonId);
  const data = query.data;
  const nowMs = useServerNow(data?.generated_at, query.dataUpdatedAt);
  if (!data) {
    return (
      <>
        <BackTop fallback="/subjects" />
        {query.isPending ? <Skeleton label="Загрузка" /> : null}
        {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
      </>
    );
  }
  const homework = data.homework.filter((item) => item.my_state.completion !== 'completed' && !isHomeworkArchived(item, nowMs, data.group_timezone));
  return (
    <SubjectView
      name={data.lesson.subject}
      timeZone={data.group_timezone}
      nowMs={nowMs}
      lessons={[data.lesson]}
      homework={homework}
      events={data.events_state === 'ready' ? (data.events ?? []) : null}
      materials={data.materials}
    />
  );
}
