import { useNavigate, useParams, useSearchParams } from 'react-router';
import { useAcademicDeadlineQuery, useCreateEvent, useCreateHomework, useEditEventMutation, useEditHomeworkMutation, useHomeworkQuery, useSubjectsQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { useHasPermission } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Empty, Skeleton } from '../../ui/Blocks';
import { useFeedback } from '../../ui/Feedback';
import { RequestError } from '../../ui/RequestError';
import { CardEditForm, type CardEditPatch } from './CardEditForm';

/** Subject names the group already uses, for the datalist of the form. */
function useSubjectNames(): string[] {
  const subjects = useSubjectsQuery();
  return subjects.data?.items.map((item) => item.name) ?? [];
}

const forbidden = <Empty title="Нет доступа" body={ru.edit.errors.forbidden} />;

export function HomeworkEditScreen() {
  const { homeworkId = '' } = useParams();
  const navigate = useNavigate();
  const { toast } = useFeedback();
  const back = () => void navigate(`/homework/${homeworkId}`, { replace: true });
  const query = useHomeworkQuery(homeworkId);
  const mutation = useEditHomeworkMutation(homeworkId);
  const names = useSubjectNames();
  const timeZone = useGroupTimeZone();
  const detail = query.data;
  const save = (patch: CardEditPatch) => {
    if (!detail) return;
    const { subject, urgency, ...rest } = patch;
    mutation.mutate(
      {
        expected_revision: detail.revision,
        ...(rest.title !== undefined ? { title: rest.title } : {}),
        ...(rest.description !== undefined ? { description: rest.description } : {}),
        ...(subject !== undefined ? { subject_name: subject } : {}),
        ...(rest.deadline !== undefined ? { deadline: rest.deadline } : {}),
        ...(urgency !== undefined ? { urgency } : {}),
      },
      { onSuccess: () => { toast(ru.edit.saved); back(); } },
    );
  };
  return (
    <>
      <BackTop fallback={`/homework/${homeworkId}`} />
      {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
      {query.isError && !detail ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
      {detail && !detail.permissions.includes('homework.edit') ? forbidden : null}
      {detail && detail.permissions.includes('homework.edit') ? (
        <CardEditForm
          key={detail.revision}
          variant="homework"
          initial={{
            title: detail.title,
            subject: detail.confirmed_subject?.name ?? detail.subject.name,
            description: detail.description ?? '',
            deadline: { at: detail.deadline.at ?? null, dateOnly: detail.deadline.date_only },
            urgent: detail.urgency !== 'normal',
          }}
          timeZone={timeZone}
          subjectSuggestions={names}
          saving={mutation.isPending}
          error={mutation.error}
          onSave={save}
          onCancel={back}
        />
      ) : null}
    </>
  );
}

export function EventEditScreen() {
  const { deadlineId = '' } = useParams();
  const navigate = useNavigate();
  const { toast } = useFeedback();
  const back = () => void navigate(`/deadlines/${deadlineId}`, { replace: true });
  const query = useAcademicDeadlineQuery(deadlineId);
  const mutation = useEditEventMutation(deadlineId);
  const names = useSubjectNames();
  const data = query.data;
  const item = data?.item;
  const allowed = data?.permissions?.includes('homework.edit') ?? false;
  const save = (patch: CardEditPatch) => {
    if (item?.revision === undefined) return;
    mutation.mutate({ expected_revision: item.revision, ...patch }, { onSuccess: () => { toast(ru.edit.saved); back(); } });
  };
  return (
    <>
      <BackTop fallback={`/deadlines/${deadlineId}`} />
      {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
      {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
      {data && (!allowed || item?.revision === undefined) ? forbidden : null}
      {data && item && allowed && item.revision !== undefined ? (
        <CardEditForm
          key={item.revision}
          variant="event"
          initial={{
            title: item.title,
            subject: item.confirmed_subject?.name ?? item.subject,
            description: item.description,
            kind: item.kind,
            dateHint: item.date_hint ?? '',
            deadline: { at: item.deadline_at, dateOnly: item.date_only },
          }}
          timeZone={data.group_timezone}
          subjectSuggestions={names}
          saving={mutation.isPending}
          error={mutation.error}
          onSave={save}
          onCancel={back}
        />
      ) : null}
    </>
  );
}

/** A new homework card or event: `?type=event` opens the event form, anything else the homework one. */
export function CreateCardScreen() {
  const [params] = useSearchParams();
  const event = params.get('type') === 'event';
  const navigate = useNavigate();
  const { toast } = useFeedback();
  const allowed = useHasPermission('homework.create');
  const names = useSubjectNames();
  const timeZone = useGroupTimeZone();
  const createHomework = useCreateHomework();
  const createEvent = useCreateEvent();
  const mutation = event ? createEvent : createHomework;
  const back = event ? '/deadlines' : '/tasks';

  if (!allowed) {
    return (
      <>
        <BackTop fallback={back} />
        <Empty title="Нет доступа" body={ru.edit.create.forbidden} />
      </>
    );
  }
  const save = (patch: CardEditPatch) => {
    const deadline = patch.deadline ?? undefined;
    if (event) {
      createEvent.mutate(
        {
          title: patch.title ?? '',
          subject: patch.subject ?? '',
          kind: patch.kind ?? 'assessment',
          ...(patch.description !== undefined ? { description: patch.description } : {}),
          ...(patch.date_hint ? { date_hint: patch.date_hint } : {}),
          ...(deadline ? { deadline } : {}),
        },
        { onSuccess: (data) => { toast('Карточка создана'); void navigate(`/deadlines/${data.item.id}`, { replace: true }); } },
      );
    } else {
      createHomework.mutate(
        {
          title: patch.title ?? '',
          subject: patch.subject ?? '',
          ...(patch.description !== undefined ? { description: patch.description } : {}),
          ...(deadline ? { deadline } : {}),
        },
        { onSuccess: (data) => { toast('Карточка создана'); void navigate(`/homework/${data.id}`, { replace: true }); } },
      );
    }
  };
  return (
    <>
      <BackTop fallback={back} />
      <CardEditForm
        key={event ? 'event' : 'homework'}
        variant={event ? 'event' : 'homework'}
        purpose="create"
        initial={{ title: '', subject: '', description: '', kind: event ? 'assessment' : undefined, deadline: { at: null, dateOnly: false } }}
        timeZone={timeZone}
        subjectSuggestions={names}
        saving={mutation.isPending}
        error={mutation.error}
        onSave={save}
        onCancel={() => void navigate(back)}
      />
    </>
  );
}
