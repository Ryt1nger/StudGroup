import { useParams } from 'react-router';
import { useAcademicDeadlinesQuery, useScheduleQuery, useSubjectsQuery } from '../../api/queries';
import { dayKey } from '../../lib/format';
import { isHomeworkArchived } from '../../lib/homeworkArchive';
import { useServerNow } from '../../lib/hooks';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Empty, Skeleton } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { confirmedSubject } from '../homework/confirmedSubject';
import { weekKeys } from '../shared/WeekStrip';
import { useTaskList } from '../tasks/useTaskList';
import { SubjectView } from './SubjectView';

/** A subject picked in «Предметы»: assembled from the lists the app already loads, filtered by name. */
export function SubjectScreen() {
  const { subjectId = '' } = useParams();
  const subjects = useSubjectsQuery();
  const timeZone = useGroupTimeZone();
  const nowMs = useServerNow(subjects.data?.generated_at, subjects.dataUpdatedAt);
  const days = weekKeys(dayKey(nowMs, timeZone));
  const schedule = useScheduleQuery(days[0]!, days[6]!);
  const deadlines = useAcademicDeadlinesQuery();
  const tasks = useTaskList('all');
  const subject = subjects.data?.items.find((item) => item.id === subjectId);

  if (subjects.isError) return <><BackTop fallback="/subjects" /><RequestError error={subjects.error} onRetry={() => void subjects.refetch()} homeLink /></>;
  if (!subjects.data) return <><BackTop fallback="/subjects" /><Skeleton label="Загрузка" /></>;
  if (!subject) return <><BackTop fallback="/subjects" /><Empty title="Предмет не найден" body="Возможно, он удалён или недоступен в вашей группе." /></>;

  const same = (value: string | undefined) => value?.trim().toLowerCase() === subject.name.trim().toLowerCase();
  const tz = tasks.timeZone ?? timeZone;
  return (
    <SubjectView
      name={subject.name}
      timeZone={tz}
      nowMs={nowMs}
      lessons={(schedule.data?.lessons ?? []).filter((lesson) => same(lesson.subject))}
      homework={tasks.items.filter((item) => same((confirmedSubject(item) ?? item.subject).name) && item.my_state.completion !== 'completed' && !isHomeworkArchived(item, nowMs, tz))}
      events={deadlines.data ? deadlines.data.items.filter((item) => same(item.confirmed_subject?.name ?? item.subject)) : []}
    />
  );
}
