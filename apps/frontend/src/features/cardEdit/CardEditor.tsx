import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import type { AcademicDeadline, HomeworkDetail } from '@studgroup/shared-types';
import { api, useEditEventMutation, useEditHomeworkMutation, useSubjectsQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { isAllowedTelegramUrl } from '../../lib/telegramUrl';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Button } from '../../ui/Button';
import { Paperclip, Pencil } from '../../ui/icons';
import styles from './CardEditor.module.css';
import { CardEditForm, type CardEditPatch } from './CardEditForm';

/** Subject names the group already uses, for the datalist of the form. */
function useSubjectNames(): string[] {
  const subjects = useSubjectsQuery();
  return subjects.data?.items.map((item) => item.name) ?? [];
}

/**
 * "Добавить файл": the file stays in Telegram, so this opens the bot, which waits for it and attaches
 * it to the card after the headman confirms. The deep link comes from the server (it knows the bot).
 */
function AddFileButton({ fetchLink }: { fetchLink: () => Promise<string> }) {
  const { adapter } = useTelegram();
  const open = useMutation({
    mutationFn: fetchLink,
    onSuccess: (url) => {
      if (isAllowedTelegramUrl(url)) adapter?.openTelegramLink(url);
    },
  });
  return (
    <>
      <Button
        variant="secondary"
        icon={<Paperclip size={18} />}
        busy={open.isPending}
        onClick={() => open.mutate()}
      >
        {ru.edit.addFile}
      </Button>
      {open.isError ? (
        <p role="alert" className={styles.note}>
          {ru.edit.errors.fileLink}
        </p>
      ) : null}
    </>
  );
}

/** "Редактировать" for a homework card; the server still decides who may save. */
export function HomeworkEditor({ detail, timeZone }: { detail: HomeworkDetail; timeZone: string }) {
  const [open, setOpen] = useState(false);
  const mutation = useEditHomeworkMutation(detail.id);
  const names = useSubjectNames();
  if (!detail.permissions.includes('homework.edit')) return null;
  if (!open) {
    return (
      <div className={styles.buttons}>
        <Button variant="secondary" icon={<Pencil size={18} />} onClick={() => setOpen(true)}>
          {ru.edit.open}
        </Button>
        <AddFileButton fetchLink={() => api.homeworkFileLink(detail.id)} />
      </div>
    );
  }
  const save = (patch: CardEditPatch) => {
    const { subject, ...rest } = patch;
    mutation.mutate(
      {
        expected_revision: detail.revision,
        ...(rest.title !== undefined ? { title: rest.title } : {}),
        ...(rest.description !== undefined ? { description: rest.description } : {}),
        ...(subject !== undefined ? { subject_name: subject } : {}),
        ...(rest.deadline !== undefined ? { deadline: rest.deadline } : {}),
      },
      { onSuccess: () => setOpen(false) },
    );
  };
  return (
    <CardEditForm
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
      onCancel={() => {
        mutation.reset();
        setOpen(false);
      }}
    />
  );
}

/** Same for an academic event (test, control point, ...). */
export function EventEditor({
  item,
  allowed,
  timeZone,
}: {
  item: AcademicDeadline;
  allowed: boolean;
  timeZone: string;
}) {
  const [open, setOpen] = useState(false);
  const mutation = useEditEventMutation(item.id);
  const names = useSubjectNames();
  if (!allowed || item.revision === undefined) return null;
  if (!open) {
    return (
      <div className={styles.buttons}>
        <Button variant="secondary" icon={<Pencil size={18} />} onClick={() => setOpen(true)}>
          {ru.edit.open}
        </Button>
        <AddFileButton fetchLink={() => api.eventFileLink(item.id)} />
      </div>
    );
  }
  const revision = item.revision;
  const save = (patch: CardEditPatch) => {
    mutation.mutate({ expected_revision: revision, ...patch }, { onSuccess: () => setOpen(false) });
  };
  return (
    <CardEditForm
      variant="event"
      initial={{
        title: item.title,
        subject: item.confirmed_subject?.name ?? item.subject,
        description: item.description,
        kind: item.kind,
        dateHint: item.date_hint ?? '',
        deadline: { at: item.deadline_at, dateOnly: item.date_only },
      }}
      timeZone={timeZone}
      subjectSuggestions={names}
      saving={mutation.isPending}
      error={mutation.error}
      onSave={save}
      onCancel={() => {
        mutation.reset();
        setOpen(false);
      }}
    />
  );
}
