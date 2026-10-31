import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import {
  useAcademicDeadlineQuery,
  useCreateEvent,
  useCreateHomework,
  useEditEventMutation,
  useEditHomeworkMutation,
  useHomeworkQuery,
  useSubjectsQuery,
} from '../../api/queries';
import { ru } from '../../i18n/ru';
import { useHasPermission } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Empty, Skeleton } from '../../ui/Blocks';
import { useFeedback } from '../../ui/Feedback';
import { RequestError } from '../../ui/RequestError';
import { CardEditForm, type CardEditPatch } from './CardEditForm';
import { changeMaterial } from './materialApi';
import type { PendingMaterial } from './MaterialEditor';

async function savePending(
  kind: 'homework' | 'deadline',
  id: string,
  revision: number,
  pending: PendingMaterial[],
) {
  for (const item of pending) {
    const result = await changeMaterial(kind, id, revision, item.file ? 'upload' : 'add', item);
    revision = result.revision;
  }
}

/** Subject names the group already uses, for the datalist of the form. */
function useSubjectNames(): string[] {
  const subjects = useSubjectsQuery();
  return subjects.data?.items.map((item) => item.name) ?? [];
}

const forbidden = <Empty title="Нет доступа" body={ru.edit.errors.forbidden} />;

export function HomeworkEditScreen() {
  const { homeworkId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  const [materialChanged, setMaterialChanged] = useState(false);
  const back = (forceRefresh = false) => {
    if (materialChanged || forceRefresh)
      void queryClient.invalidateQueries({ queryKey: ['homework', homeworkId] });
    void navigate(`/homework/${homeworkId}`, { replace: true });
  };
  const query = useHomeworkQuery(homeworkId);
  const mutation = useEditHomeworkMutation(homeworkId);
  const names = useSubjectNames();
  const timeZone = useGroupTimeZone();
  const [materialError, setMaterialError] = useState<unknown>(null);
  const [savingMaterials, setSavingMaterials] = useState(false);
  const detail = query.data;
  const save = async (patch: CardEditPatch, pending: PendingMaterial[], revision?: number) => {
    if (!detail) return;
    const { subject, urgency, ...rest } = patch;
    setSavingMaterials(true);
    setMaterialError(null);
    try {
      let current = revision ?? detail.revision;
      if (Object.keys(patch).length) {
        const result = await mutation.mutateAsync({
          expected_revision: current,
          ...(rest.title !== undefined ? { title: rest.title } : {}),
          ...(rest.description !== undefined ? { description: rest.description } : {}),
          ...(subject !== undefined ? { subject_name: subject } : {}),
          ...(rest.deadline !== undefined ? { deadline: rest.deadline } : {}),
          ...(urgency !== undefined ? { urgency } : {}),
        });
        current = result.revision;
      }
      await savePending('homework', homeworkId, current, pending);
      toast(ru.edit.saved);
      back(pending.length > 0);
    } catch (error) {
      setMaterialError(error);
    } finally {
      setSavingMaterials(false);
    }
  };
  return (
    <>
      <BackTop fallback={`/homework/${homeworkId}`} />
      {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
      {query.isError && !detail ? (
        <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
      ) : null}
      {detail && !detail.permissions.includes('homework.edit') ? forbidden : null}
      {detail && detail.permissions.includes('homework.edit') ? (
        <CardEditForm
          key={detail.revision}
          variant="homework"
          cardId={homeworkId}
          revision={detail.revision}
          materials={detail.materials}
          onMaterialChanged={() => setMaterialChanged(true)}
          initial={{
            title: detail.title,
            subject: detail.confirmed_subject?.name ?? detail.subject.name,
            description: detail.description ?? '',
            deadline: { at: detail.deadline.at ?? null, dateOnly: detail.deadline.date_only },
            urgent: detail.urgency !== 'normal',
          }}
          timeZone={timeZone}
          subjectSuggestions={names}
          saving={mutation.isPending || savingMaterials}
          error={materialError ?? mutation.error}
          onSave={save}
          onCancel={() => back()}
        />
      ) : null}
    </>
  );
}

export function EventEditScreen() {
  const { deadlineId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  const [materialChanged, setMaterialChanged] = useState(false);
  const back = (forceRefresh = false) => {
    if (materialChanged || forceRefresh)
      void queryClient.invalidateQueries({ queryKey: ['academic-deadline', deadlineId] });
    void navigate(`/deadlines/${deadlineId}`, { replace: true });
  };
  const query = useAcademicDeadlineQuery(deadlineId);
  const mutation = useEditEventMutation(deadlineId);
  const names = useSubjectNames();
  const [materialError, setMaterialError] = useState<unknown>(null);
  const [savingMaterials, setSavingMaterials] = useState(false);
  const data = query.data;
  const item = data?.item;
  const allowed = data?.permissions?.includes('homework.edit') ?? false;
  const save = async (patch: CardEditPatch, pending: PendingMaterial[], revision?: number) => {
    if (item?.revision === undefined) return;
    setSavingMaterials(true);
    setMaterialError(null);
    try {
      let current = revision ?? item.revision;
      if (Object.keys(patch).length) {
        const result = await mutation.mutateAsync({ expected_revision: current, ...patch });
        current = result.item.revision ?? current;
      }
      await savePending('deadline', deadlineId, current, pending);
      toast(ru.edit.saved);
      back(pending.length > 0);
    } catch (error) {
      setMaterialError(error);
    } finally {
      setSavingMaterials(false);
    }
  };
  return (
    <>
      <BackTop fallback={`/deadlines/${deadlineId}`} />
      {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
      {query.isError && !data ? (
        <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
      ) : null}
      {data && (!allowed || item?.revision === undefined) ? forbidden : null}
      {data && item && allowed && item.revision !== undefined ? (
        <CardEditForm
          key={item.revision}
          variant="event"
          cardId={deadlineId}
          revision={item.revision}
          materials={item.materials}
          onMaterialChanged={() => setMaterialChanged(true)}
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
          saving={mutation.isPending || savingMaterials}
          error={materialError ?? mutation.error}
          onSave={save}
          onCancel={() => back()}
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
  const queryClient = useQueryClient();
  const { toast } = useFeedback();
  const allowed = useHasPermission('homework.create');
  const names = useSubjectNames();
  const timeZone = useGroupTimeZone();
  const createHomework = useCreateHomework();
  const createEvent = useCreateEvent();
  const mutation = event ? createEvent : createHomework;
  const [materialError, setMaterialError] = useState<unknown>(null);
  const [savingMaterials, setSavingMaterials] = useState(false);
  const back = event ? '/deadlines' : '/tasks';

  if (!allowed) {
    return (
      <>
        <BackTop fallback={back} />
        <Empty title="Нет доступа" body={ru.edit.create.forbidden} />
      </>
    );
  }
  const save = async (patch: CardEditPatch, pending: PendingMaterial[]) => {
    const deadline = patch.deadline ?? undefined;
    setSavingMaterials(true);
    setMaterialError(null);
    let createdPath: string | null = null;
    try {
      if (event) {
        const data = await createEvent.mutateAsync({
          title: patch.title ?? '',
          subject: patch.subject ?? '',
          kind: patch.kind ?? 'assessment',
          ...(patch.description !== undefined ? { description: patch.description } : {}),
          ...(patch.date_hint ? { date_hint: patch.date_hint } : {}),
          ...(deadline ? { deadline } : {}),
        });
        createdPath = `/deadlines/${data.item.id}`;
        await savePending('deadline', data.item.id, data.item.revision ?? 1, pending);
        if (pending.length)
          void queryClient.invalidateQueries({ queryKey: ['academic-deadline', data.item.id] });
        toast('Карточка создана');
        void navigate(`/deadlines/${data.item.id}`, { replace: true });
      } else {
        const data = await createHomework.mutateAsync({
          title: patch.title ?? '',
          subject: patch.subject ?? '',
          ...(patch.description !== undefined ? { description: patch.description } : {}),
          ...(deadline ? { deadline } : {}),
        });
        createdPath = `/homework/${data.id}`;
        await savePending('homework', data.id, data.revision, pending);
        if (pending.length) void queryClient.invalidateQueries({ queryKey: ['homework', data.id] });
        toast('Карточка создана');
        void navigate(`/homework/${data.id}`, { replace: true });
      }
    } catch (error) {
      if (createdPath) {
        toast('Карточка создана, но не все материалы загрузились. Добавьте их при редактировании.');
        void navigate(`${createdPath}/edit`, { replace: true });
      } else setMaterialError(error);
    } finally {
      setSavingMaterials(false);
    }
  };
  return (
    <>
      <BackTop fallback={back} />
      <CardEditForm
        key={event ? 'event' : 'homework'}
        variant={event ? 'event' : 'homework'}
        purpose="create"
        initial={{
          title: '',
          subject: '',
          description: '',
          kind: event ? 'assessment' : undefined,
          deadline: { at: null, dateOnly: false },
        }}
        timeZone={timeZone}
        subjectSuggestions={names}
        saving={mutation.isPending || savingMaterials}
        error={materialError ?? mutation.error}
        onSave={save}
        onCancel={() => void navigate(back)}
      />
    </>
  );
}
