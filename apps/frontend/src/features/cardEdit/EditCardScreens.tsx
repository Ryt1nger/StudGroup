import type { ReactNode } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import clsx from 'clsx';
import {
  useAcademicDeadlineQuery,
  useCreateEventMutation,
  useCreateHomeworkMutation,
  useDeleteEventMutation,
  useDeleteHomeworkMutation,
  useEditEventMutation,
  useEditHomeworkMutation,
  useHomeworkQuery,
  useSubjectsQuery,
} from '../../api/queries';
import { ru } from '../../i18n/ru';
import { useHasPermission } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { useBackButton } from '../../shell/useBackButton';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { ChevronLeft } from '../../ui/icons';
import layout from '../homework/HomeworkScreen.module.css';
import { CardEditForm, type CardEditPatch, type CardEditValues } from './CardEditForm';
import styles from './EditCardScreens.module.css';

/** Subject names the group already uses, for the datalist of the form. */
function useSubjectNames(): string[] {
  const subjects = useSubjectsQuery();
  return subjects.data?.items.map((item) => item.name) ?? [];
}

/** Own page for editing: back returns to the card, saving does the same. */
function EditPage({
  children,
  onBack,
  title = ru.edit.pageTitle,
}: {
  children: ReactNode;
  onBack: () => void;
  title?: string;
}) {
  const nativeBack = useBackButton(onBack);
  return (
    <div className={layout.page}>
      <header className={layout.header}>
        {!nativeBack ? (
          <button type="button" className={layout.back} onClick={onBack} aria-label={ru.detail.back}>
            <ChevronLeft size={24} />
          </button>
        ) : (
          <span className={layout.backSpacer} />
        )}
        <h1 className={layout.headerTitle}>{title}</h1>
        <span className={layout.backSpacer} />
      </header>
      <div className={layout.stack}>{children}</div>
    </div>
  );
}

function Forbidden() {
  return (
    <p role="alert" className={layout.muted}>
      {ru.edit.errors.forbidden}
    </p>
  );
}

export function HomeworkEditScreen() {
  const { homeworkId = '' } = useParams();
  const navigate = useNavigate();
  const back = () => void navigate(`/homework/${homeworkId}`, { replace: true });
  const query = useHomeworkQuery(homeworkId);
  const mutation = useEditHomeworkMutation(homeworkId);
  const removal = useDeleteHomeworkMutation(homeworkId);
  const names = useSubjectNames();
  const timeZone = useGroupTimeZone();
  const detail = query.data;
  const save = (patch: CardEditPatch) => {
    if (!detail) return;
    const { subject, ...rest } = patch;
    mutation.mutate(
      {
        expected_revision: detail.revision,
        ...(rest.title !== undefined ? { title: rest.title } : {}),
        ...(rest.description !== undefined ? { description: rest.description } : {}),
        ...(subject !== undefined ? { subject_name: subject } : {}),
        ...(rest.deadline !== undefined ? { deadline: rest.deadline } : {}),
      },
      { onSuccess: back },
    );
  };
  return (
    <EditPage onBack={back}>
      {query.isPending ? <Skeleton height={320} /> : null}
      {query.isError && !detail ? (
        <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
      ) : null}
      {detail && !detail.permissions.includes('homework.edit') ? <Forbidden /> : null}
      {detail && detail.permissions.includes('homework.edit') ? (
        <CardEditForm
          key={detail.revision}
          variant="homework"
          initial={{
            title: detail.title,
            subject: detail.confirmed_subject?.name ?? detail.subject.name,
            description: detail.description ?? '',
            deadline: { at: detail.deadline.at ?? null, dateOnly: detail.deadline.date_only },
          }}
          timeZone={timeZone}
          subjectSuggestions={names}
          saving={mutation.isPending}
          error={mutation.error}
          onSave={save}
          onCancel={back}
          deleting={removal.isPending}
          deleteError={removal.error}
          onDelete={() =>
            removal.mutate(detail.revision, { onSuccess: () => void navigate('/tasks', { replace: true }) })
          }
        />
      ) : null}
    </EditPage>
  );
}

export function EventEditScreen() {
  const { deadlineId = '' } = useParams();
  const navigate = useNavigate();
  const back = () => void navigate(`/deadlines/${deadlineId}`, { replace: true });
  const query = useAcademicDeadlineQuery(deadlineId);
  const mutation = useEditEventMutation(deadlineId);
  const removal = useDeleteEventMutation(deadlineId);
  const names = useSubjectNames();
  const data = query.data;
  const item = data?.item;
  const allowed = data?.permissions?.includes('homework.edit') ?? false;
  const save = (patch: CardEditPatch) => {
    if (item?.revision === undefined) return;
    mutation.mutate({ expected_revision: item.revision, ...patch }, { onSuccess: back });
  };
  return (
    <EditPage onBack={back}>
      {query.isPending ? <Skeleton height={320} /> : null}
      {query.isError && !data ? (
        <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
      ) : null}
      {data && (!allowed || item?.revision === undefined) ? <Forbidden /> : null}
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
          deleting={removal.isPending}
          deleteError={removal.error}
          onDelete={() => {
            if (item.revision === undefined) return;
            removal.mutate(item.revision, {
              onSuccess: () => void navigate('/deadlines', { replace: true }),
            });
          }}
        />
      ) : null}
    </EditPage>
  );
}

const EMPTY: CardEditValues = {
  title: '',
  subject: '',
  description: '',
  deadline: { at: null, dateOnly: false },
};

/**
 * «+»: a headman or deputy makes a card in the Mini App instead of through the bot. The type comes
 * from `?type=` (the plus button preselects the open tab); the new card opens after creating.
 */
export function CreateCardScreen() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const type = params.get('type') === 'event' ? 'event' : 'homework';
  const allowed = useHasPermission('homework.create');
  const homework = useCreateHomeworkMutation();
  const event = useCreateEventMutation();
  const names = useSubjectNames();
  const timeZone = useGroupTimeZone();
  const back = () => void navigate(type === 'event' ? '/deadlines' : '/tasks', { replace: true });
  const active = type === 'event' ? event : homework;

  const save = (patch: CardEditPatch) => {
    const common = {
      title: patch.title ?? '',
      subject: patch.subject ?? '',
      ...(patch.description ? { description: patch.description } : {}),
      ...(patch.deadline ? { deadline: patch.deadline } : {}),
    };
    if (type === 'homework') {
      homework.mutate(common, {
        onSuccess: (detail) => void navigate(`/homework/${detail.id}`, { replace: true }),
      });
      return;
    }
    event.mutate(
      {
        ...common,
        ...(patch.kind ? { kind: patch.kind } : {}),
        ...(patch.date_hint ? { date_hint: patch.date_hint } : {}),
      },
      { onSuccess: (response) => void navigate(`/deadlines/${response.item.id}`, { replace: true }) },
    );
  };

  return (
    <EditPage onBack={back} title={ru.edit.create.pageTitle}>
      {!allowed ? (
        <p role="alert" className={layout.muted}>
          {ru.edit.create.forbidden}
        </p>
      ) : (
        <>
          <div className={styles.types} role="radiogroup" aria-label={ru.edit.create.type}>
            {(['homework', 'event'] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={type === value}
                className={clsx(styles.type, type === value && styles.typeActive)}
                onClick={() => setParams(value === 'event' ? { type: 'event' } : {}, { replace: true })}
              >
                {ru.edit.create[value]}
              </button>
            ))}
          </div>
          <CardEditForm
            key={type}
            variant={type}
            mode="create"
            initial={type === 'event' ? { ...EMPTY, kind: 'control_point', dateHint: '' } : EMPTY}
            timeZone={timeZone}
            subjectSuggestions={names}
            saving={active.isPending}
            error={active.error}
            onSave={save}
            onCancel={back}
          />
        </>
      )}
    </EditPage>
  );
}
